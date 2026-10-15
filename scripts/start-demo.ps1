<#
.SYNOPSIS
    ToolLightHouse Secure Windows Demo Launcher.
.DESCRIPTION
    Production-ready launcher for multi-device demo presentations:
    1. Resolves repository root and restores location safely via push/pop location.
    2. Uses existing Python virtual environment with worktree PYTHONPATH isolation.
    3. Requires compiled frontend (frontend/dist/index.html).
    4. Rejects occupied ports and duplicate server instances (process inspection, no kill).
    5. Validates TLS certificate and private key match via scripts/demo_tls_check.py,
       checks exact IP SAN matching (not substring!), NotBefore/NotAfter validity.
    6. Displays exact LAN dashboard URL; validates localhost DNS and loopback IP before printing.
    7. Loads persistent configuration envelope (config/app_config.json) and semantically
       validates settings values via Settings.from_file BEFORE prompting for credentials;
       fails closed on corrupt or semantically invalid configuration with actionable recovery.
    8. Handles credentials securely via Read-Host -AsSecureString in memory only,
       requiring both non-empty operator and admin keys for demo operation. Strictly cleans up
       and restores environment variables in finally block.
    9. Defaults to '--mode real' with safe_startup and simulated serial rather than
       misleading mock detections, explaining preparation workflow; allows explicit -Mock.
    10. Propagates Python server exit code.
#>

[CmdletBinding()]
param (
    [int]$Port = 8000,
    [string]$LanIP = "",
    [string]$HostAddress = "0.0.0.0",
    [string]$Mode = "real",
    [switch]$Mock = $false,
    [string]$ConfigPath = "config/app_config.json",
    [string]$CertDir = "certificates",
    [string]$Serial = "",
    [switch]$ConfirmInventory = $false,
    [switch]$NonInteractive = $false,
    [switch]$DryRun = $false
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "network_helpers.ps1")
Push-Location $repoRoot

$priorPairing = $env:TOOLLIGHTHOUSE_PAIRING_KEY
$priorAdmin = $env:TOOLLIGHTHOUSE_ADMIN_KEY
$priorPythonPath = $env:PYTHONPATH

try {
    Write-Host "`n==========================================================" -ForegroundColor Cyan
    Write-Host " ToolLightHouse Demo Launcher                             " -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host "Working Directory: $repoRoot" -ForegroundColor DarkGray

    # 1. Locate existing Python Virtual Environment (Strict venv requirement; no silent arbitrary global python fallback)
    $pythonExe = $null
    $venvCandidates = @(
        (Join-Path $repoRoot ".venv\Scripts\python.exe"),
        (Join-Path $repoRoot ".venv-dev\Scripts\python.exe")
    )

    if ($env:VIRTUAL_ENV) {
        $venvCandidates += (Join-Path $env:VIRTUAL_ENV "Scripts\python.exe")
    }
    if ($env:PYTHON_EXECUTABLE) {
        $venvCandidates += $env:PYTHON_EXECUTABLE
    }

    try {
        $commonDir = git rev-parse --git-common-dir 2>$null
        if ($commonDir) {
            $parentRepo = Split-Path -Parent $commonDir
            $venvCandidates += (Join-Path $parentRepo ".venv\Scripts\python.exe")
            $venvCandidates += (Join-Path $parentRepo ".venv-dev\Scripts\python.exe")
        }
    } catch {}

    foreach ($cand in $venvCandidates) {
        if ($cand -and (Test-Path $cand)) {
            $pythonExe = $cand
            break
        }
    }

    if (-not $pythonExe) {
        Write-Host "[ERROR] Python virtual environment (.venv) not found." -ForegroundColor Red
        Write-Host "Create it with: python -m venv .venv and install dependencies." -ForegroundColor Yellow
        exit 1
    }

    # Set PYTHONPATH strictly for this execution; will be restored in finally block
    $env:PYTHONPATH = (Join-Path $repoRoot "src")

    # 2. Verify Compiled Frontend Build (Mandatory in repository worktree)
    $distIndex = Join-Path $repoRoot "frontend\dist\index.html"
    if (-not (Test-Path $distIndex)) {
        Write-Host "[ERROR] Compiled frontend is mandatory for demo operations ('frontend/dist/index.html' not found)." -ForegroundColor Red
        Write-Host "Run 'cd frontend; npm run build' before starting the demo." -ForegroundColor Yellow
        exit 1
    }

    # 3. Check for Occupied Port & Duplicate Server Processes (No Kill)
    $portOccupied = $false
    $occupyingPid = $null
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if ($conn) {
            $portOccupied = $true
            $occupyingPid = ($conn | Select-Object -First 1).OwningProcess
        }
    } catch {
        $ns = netstat -ano | Select-String ":$Port " | Select-String "LISTENING"
        if ($ns) {
            $portOccupied = $true
            $occupyingPid = ($ns -split '\s+')[-1]
        }
    }

    if ($portOccupied) {
        Write-Host "[ERROR] Port $Port is already occupied by PID $occupyingPid." -ForegroundColor Red
        Write-Host "Terminate the occupying process or run start-demo with -Port <port>." -ForegroundColor Yellow
        exit 1
    }

    $duplicateProc = $null
    if (-not $env:TOOLLIGHTHOUSE_IGNORE_DUPLICATE_PROCESS) {
        try {
            $pyProcs = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue
            foreach ($p in $pyProcs) {
                if ($p.ProcessId -eq $PID) { continue }
                $cmd = $p.CommandLine
                if ($cmd -match '\btoollighthouse\b.*?\b(serve|run)\b' -or $cmd -match '\buvicorn\b.*?\btoollighthouse\b') {
                    $duplicateProc = $p
                    break
                }
            }
        } catch {}
    }

    if ($duplicateProc) {
        Write-Host "[ERROR] Duplicate ToolLightHouse instance already running (PID: $($duplicateProc.ProcessId))." -ForegroundColor Red
        Write-Host "Rejecting startup to prevent GPU and COM port competition. Please terminate the existing instance." -ForegroundColor Yellow
        exit 1
    }

    # 4. Detect LAN IPv4 Address
    $resolvedLanIP = $null
    if ($LanIP) {
        $override = Resolve-ToolLightHouseLanIPv4 -ExplicitIP $LanIP
        if ($override.InvalidExplicit) {
            Write-Host "[ERROR] -LanIP '$LanIP' is not a valid IPv4 address." -ForegroundColor Red
            exit 1
        }
        $resolvedLanIP = $override.IPAddress
    } elseif ($HostAddress -and $HostAddress -ne "0.0.0.0" -and $HostAddress -ne "localhost" -and $HostAddress -ne "127.0.0.1") {
        $resolvedLanIP = $HostAddress
    } elseif ($env:TOOLLIGHTHOUSE_LAN_IP) {
        $resolvedLanIP = $env:TOOLLIGHTHOUSE_LAN_IP
    } else {
        $lanResolution = Resolve-ToolLightHouseLanIPv4
        $resolvedLanIP = $lanResolution.IPAddress
        if ($lanResolution.Ambiguous) {
            Write-Host "[ERROR] Multiple reachable LAN addresses found; no primary was guessed: $($lanResolution.Candidates -join ', ')." -ForegroundColor Red
            Write-Host "Specify the address reachable by tablet and phone with -LanIP <IPv4>." -ForegroundColor Yellow
            exit 1
        }
    }

    if (-not $resolvedLanIP -or $resolvedLanIP -eq "127.0.0.1") {
        Write-Host "[ERROR] No non-virtual LAN IPv4 detected. Tablets and mobile devices cannot reach '127.0.0.1'." -ForegroundColor Red
        Write-Host "Connect to a local Wi-Fi or mobile hotspot, or specify the exact LAN IP with -LanIP <IPv4>." -ForegroundColor Yellow
        exit 1
    }

    # 5. Validate TLS Certificates, Private Key Match & Exact SAN via demo_tls_check.py
    $resolvedCertDir = if ([System.IO.Path]::IsPathRooted($CertDir)) { $CertDir } else { Join-Path $repoRoot $CertDir }
    $certFile = Join-Path $resolvedCertDir "lan-cert.pem"
    $keyFile = Join-Path $resolvedCertDir "lan-key.pem"
    $tlsHelper = Join-Path $repoRoot "scripts\demo_tls_check.py"

    if ((-not (Test-Path $certFile)) -or (-not (Test-Path $keyFile))) {
        Write-Host "[ERROR] TLS certificates not found ($certFile, $keyFile)." -ForegroundColor Red
        Write-Host "HTTPS is mandatory for mobile browser camera access (getUserMedia / WebRTC)." -ForegroundColor Yellow
        Write-Host "Run: .\scripts\setup_certs.ps1 to generate trusted LAN certificates." -ForegroundColor Yellow
        exit 1
    }

    $tlsArgs = @($tlsHelper, $certFile, $keyFile, $resolvedLanIP)
    $certCheckRaw = & $pythonExe @tlsArgs 2>$null
    $certResult = $null
    try {
        $certResult = $certCheckRaw | ConvertFrom-Json
    } catch {
        Write-Host "[ERROR] Failed to run Python SSL certificate validator: $certCheckRaw" -ForegroundColor Red
        exit 1
    }

    if (-not $certResult.valid_dates) {
        Write-Host "[ERROR] $($certResult.error)" -ForegroundColor Red
        Write-Host "Regenerate certificate: .\scripts\setup_certs.ps1 -Force" -ForegroundColor Yellow
        exit 1
    }

    if (-not $certResult.key_match) {
        Write-Host "[ERROR] $($certResult.error)" -ForegroundColor Red
        exit 1
    }

    if (-not $certResult.has_lan_ip) {
        Write-Host "[ERROR] Certificate SAN does not contain exact LAN IP '$resolvedLanIP'." -ForegroundColor Red
        Write-Host "SANs in certificate: $($certResult.sans -join ', ')" -ForegroundColor Yellow
        Write-Host "Regenerate certificate for current IP: .\scripts\setup_certs.ps1 -Force" -ForegroundColor Yellow
        exit 1
    }

    # 6. Verify and Validate Persistent Configuration Envelope & Semantic Settings BEFORE credentials prompt
    $resolvedConfig = if ([System.IO.Path]::IsPathRooted($ConfigPath)) { $ConfigPath } else { Join-Path $repoRoot $ConfigPath }
    $validConfigPath = $null

    if (Test-Path $resolvedConfig) {
        try {
            $rawJson = Get-Content $resolvedConfig -Raw | ConvertFrom-Json
            if (-not ($rawJson -and ($rawJson.PSObject.Properties['schema_version'] -or $rawJson.PSObject.Properties['mode'] -or $rawJson.PSObject.Properties['settings']))) {
                Write-Host "[ERROR] Configuration file '$resolvedConfig' exists but is not a valid configuration envelope." -ForegroundColor Red
                Write-Host "Actionable recovery: Restore from '$resolvedConfig.backup' or delete it to proceed with defaults." -ForegroundColor Yellow
                exit 1
            }
        } catch {
            Write-Host "[ERROR] Configuration file '$resolvedConfig' is corrupt or unreadable: $_" -ForegroundColor Red
            Write-Host "Actionable recovery: Check '$resolvedConfig.backup' or fix the JSON syntax before starting." -ForegroundColor Yellow
            exit 1
        }

        # Semantic validation via Settings.from_file
        $cfgValPy = @'
import sys
from pathlib import Path
repo_root = Path(sys.argv[1])
cfg_path = Path(sys.argv[2])
sys.path.insert(0, str(repo_root / "src"))
try:
    from toollighthouse.config import Settings
    settings = Settings.from_file(cfg_path)
    print("CONFIG_OK")
except Exception as e:
    print(f"CONFIG_ERROR:{e}")
    sys.exit(1)
'@
        $valRes = $cfgValPy | & $pythonExe - "$repoRoot" "$resolvedConfig" 2>$null
        if ($LASTEXITCODE -ne 0 -or ($valRes -and $valRes.Trim().StartsWith("CONFIG_ERROR:"))) {
            $errMsg = if ($valRes) { ($valRes.Trim() -replace '^CONFIG_ERROR:', '') } else { "Failed to load settings from configuration file." }
            Write-Host "[ERROR] Configuration file '$resolvedConfig' contains invalid settings: $errMsg" -ForegroundColor Red
            Write-Host "Actionable recovery: Check '$resolvedConfig.backup' or correct the configuration values before starting." -ForegroundColor Yellow
            exit 1
        }

        $validConfigPath = $resolvedConfig
        Write-Host "[+] Loaded and validated persistent configuration: $validConfigPath" -ForegroundColor Green
    } else {
        Write-Host "[i] No persistent configuration file found at '$resolvedConfig'. Using default runtime settings." -ForegroundColor DarkGray
    }

    # 7. Secure Credentials Prompt (Read-Host -AsSecureString in memory only)
    if (-not $env:TOOLLIGHTHOUSE_PAIRING_KEY) {
        if ($NonInteractive) {
            Write-Host "[ERROR] TOOLLIGHTHOUSE_PAIRING_KEY is required in NonInteractive mode." -ForegroundColor Red
            exit 1
        }
        $secPairing = Read-Host -Prompt "Enter Operator Pairing Key (Controller role for tablet)" -AsSecureString
        $bstrPair = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secPairing)
        try {
            $env:TOOLLIGHTHOUSE_PAIRING_KEY = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstrPair)
        } finally {
            [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstrPair)
        }
    }

    if (-not $env:TOOLLIGHTHOUSE_ADMIN_KEY) {
        if ($NonInteractive) {
            Write-Host "[ERROR] TOOLLIGHTHOUSE_ADMIN_KEY is required for demo operations (calibration and inventory mutation require admin role)." -ForegroundColor Red
            exit 1
        }
        $secAdmin = Read-Host -Prompt "Enter Admin Key (Administrator role required for calibration and inventory)" -AsSecureString
        $bstrAdmin = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secAdmin)
        try {
            $env:TOOLLIGHTHOUSE_ADMIN_KEY = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($bstrAdmin)
        } finally {
            [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstrAdmin)
        }
    }

    if (-not $env:TOOLLIGHTHOUSE_PAIRING_KEY) {
        Write-Host "[ERROR] Pairing key cannot be empty. Authentication is required to serve." -ForegroundColor Red
        exit 1
    }

    if (-not $env:TOOLLIGHTHOUSE_ADMIN_KEY) {
        Write-Host "[ERROR] Admin key cannot be empty. Admin authorization is required for demo calibration and inventory configuration." -ForegroundColor Red
        exit 1
    }

    # 8. Operational Mode & Preparation Notice
    $effectiveMode = if ($Mock) { "mock" } else { $Mode }
    if ($effectiveMode -eq "real") {
        Write-Host "`n[MODE] Starting in 'real' mode with safe_startup and simulated serial." -ForegroundColor Cyan
        Write-Host "       The system starts in safe preparation mode until calibration and gallery are configured." -ForegroundColor White
        Write-Host "       Connect physical Arduino Nano in the dashboard Arduino tab once inventory is confirmed." -ForegroundColor White
        Write-Host "       (To run synthetic software rehearsal instead, use -Mock).`n" -ForegroundColor DarkGray
    } else {
        Write-Host "`n[MODE] Starting in 'mock' mode for software rehearsal (synthetic detections).`n" -ForegroundColor Yellow
    }

    # 9. Assemble CLI Arguments
    $cliArgs = @(
        "-m", "toollighthouse", "serve",
        "--mode", $effectiveMode,
        "--host", $HostAddress,
        "--port", "$Port",
        "--ssl-certfile", $certFile,
        "--ssl-keyfile", $keyFile
    )

    if ($Serial) {
        if (-not $ConfirmInventory) {
            Write-Host "[ERROR] --serial requires -ConfirmInventory." -ForegroundColor Red
            exit 1
        }
        $cliArgs += @("--serial", $Serial, "--confirm-inventory")
    } else {
        $cliArgs += "--simulate-leds"
    }

    if ($validConfigPath) {
        $cliArgs += @("--config", $validConfigPath)
    }

    # 10. Display Exact LAN Dashboard URL and Localhost (Validating exact DNS vs IP SAN)
    Write-Host "==========================================================" -ForegroundColor Green
    Write-Host " ToolLightHouse Server Launching                          " -ForegroundColor Green
    Write-Host "==========================================================" -ForegroundColor Green
    Write-Host "  LAN Dashboard URL:  https://${resolvedLanIP}:${Port}/" -ForegroundColor Cyan
    if ($certResult.has_localhost_dns) {
        Write-Host "  Localhost URL:      https://localhost:${Port}/" -ForegroundColor White
    }
    if ($certResult.has_localhost_ip) {
        Write-Host "  Loopback IP URL:    https://127.0.0.1:${Port}/" -ForegroundColor White
    }
    Write-Host "==========================================================`n" -ForegroundColor Green

    if ($DryRun) {
        Write-Host "[DRY RUN] All pre-flight validations passed successfully. Exiting without launching server." -ForegroundColor Green
        exit 0
    }

    # Execute server and propagate exit code
    & $pythonExe @cliArgs
    $serverExitCode = $LASTEXITCODE
    if ($serverExitCode -ne 0) {
        exit $serverExitCode
    }

} finally {
    # Strictly restore and clean up credentials and PYTHONPATH
    if ($null -eq $priorPairing) {
        Remove-Item Env:\TOOLLIGHTHOUSE_PAIRING_KEY -ErrorAction SilentlyContinue
    } else {
        $env:TOOLLIGHTHOUSE_PAIRING_KEY = $priorPairing
    }

    if ($null -eq $priorAdmin) {
        Remove-Item Env:\TOOLLIGHTHOUSE_ADMIN_KEY -ErrorAction SilentlyContinue
    } else {
        $env:TOOLLIGHTHOUSE_ADMIN_KEY = $priorAdmin
    }

    if ($null -eq $priorPythonPath) {
        Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $priorPythonPath
    }

    Pop-Location
}
