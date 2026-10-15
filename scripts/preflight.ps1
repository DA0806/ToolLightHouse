<#
.SYNOPSIS
    ToolLightHouse Preflight Diagnostic and Environment Verification.
.DESCRIPTION
    Non-destructive audit of 12 critical subsystems required for multi-device demo operations:
    1. Python venv presence and version (strict virtualenv requirement).
    2. Import validation for core runtime dependencies.
    3. GPU / CUDA acceleration support.
    4. LAN IPv4 address detection (or explicit -LanIP override).
    5. Compiled frontend assets build presence.
    6. Configuration, inventory and calibration assets readiness via Settings.
    7. AI embedding model presence.
    8. TLS certificate, private key match, validity and exact SAN alignment via demo_tls_check.py.
    9. Port availability and duplicate server process detection.
    10. Windows Network profile on selected interface (Private vs Public).
    11. Passive firewall rule presence for port 8000 (qualified).
    12. Passive COM port detection for Arduino hardware.

    Reports meaningful PASS, WARN, FAIL ratings.
    Missing calibration/gallery assets report preparation guidance (not fake success).
    Does NOT open cameras, actuate serial ports, install packages, or modify firewall/trust settings.
    Exits with code 1 on actual blockers (FAIL > 0), 0 otherwise.
#>

[CmdletBinding()]
param (
    [int]$Port = 8000,
    [string]$LanIP = "",
    [string]$HostAddress = "",
    [string]$CertDir = "certificates",
    [string]$ConfigPath = "config/app_config.json"
)

$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "network_helpers.ps1")
$passCount = 0
$warnCount = 0
$failCount = 0

$priorPythonPath = $env:PYTHONPATH

function Report-Item {
    param (
        [string]$Name,
        [string]$Status, # PASS, WARN, FAIL
        [string]$Detail,
        [string]$Guidance = ""
    )
    switch ($Status) {
        "PASS" {
            $script:passCount = $script:passCount + 1
            Write-Host " [PASS] " -ForegroundColor Green -NoNewline
            Write-Host "$($Name): " -ForegroundColor White -NoNewline
            Write-Host $Detail -ForegroundColor Gray
        }
        "WARN" {
            $script:warnCount = $script:warnCount + 1
            Write-Host " [WARN] " -ForegroundColor Yellow -NoNewline
            Write-Host "$($Name): " -ForegroundColor White -NoNewline
            Write-Host $Detail -ForegroundColor Yellow
            if ($Guidance) {
                Write-Host "        -> $Guidance" -ForegroundColor DarkYellow
            }
        }
        "FAIL" {
            $script:failCount = $script:failCount + 1
            Write-Host " [FAIL] " -ForegroundColor Red -NoNewline
            Write-Host "$($Name): " -ForegroundColor White -NoNewline
            Write-Host $Detail -ForegroundColor Red
            if ($Guidance) {
                Write-Host "        -> $Guidance" -ForegroundColor Magenta
            }
        }
    }
}

try {
    Write-Host "`n==========================================================" -ForegroundColor Cyan
    Write-Host " ToolLightHouse Preflight Diagnostics (Read-Only Audit)   " -ForegroundColor Cyan
    Write-Host "==========================================================" -ForegroundColor Cyan
    Write-Host "Repository root: $repoRoot`n" -ForegroundColor DarkGray

    # 1. Python Virtual Environment Check (Strict venv requirement; no silent arbitrary global python fallback)
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

    if ($pythonExe) {
        $pyVer = try { (& $pythonExe -c "import sys; print(sys.version.split()[0])") } catch { "unknown" }
        Report-Item -Name "Python Venv" -Status "PASS" -Detail "Found at $pythonExe (version $pyVer)"
    } else {
        Report-Item -Name "Python Venv" -Status "FAIL" -Detail "No virtual environment found in .venv" -Guidance "Create a virtual environment: python -m venv .venv and install dependencies."
    }

    # 2. Dependency Import Validation
    if ($pythonExe) {
        $srcPath = (Join-Path $repoRoot "src") -replace '\\', '/'
        $testScript = "import sys; sys.path.insert(0, '$srcPath'); import fastapi, uvicorn, torch, cv2, serial; print('OK')"
        $importTest = try {
            & $pythonExe -c $testScript 2>&1
        } catch {
            $_
        }
        if ($importTest -match "OK") {
            Report-Item -Name "Dependencies" -Status "PASS" -Detail "FastAPI, Uvicorn, PyTorch, OpenCV, pySerial imported successfully."
        } else {
            Report-Item -Name "Dependencies" -Status "FAIL" -Detail "Import test failed: $importTest" -Guidance "Run: pip install -e . inside the active virtual environment."
        }
    } else {
        Report-Item -Name "Dependencies" -Status "FAIL" -Detail "Skipped because Python virtual environment is missing."
    }

    # 3. CUDA & GPU Acceleration
    if ($pythonExe) {
        $cudaInfo = try {
            $cudaScript = 'import torch; print(f"{torch.cuda.is_available()}|{torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}")'
            & $pythonExe -c $cudaScript 2>$null
        } catch { "False|None" }
        $parts = ($cudaInfo | Out-String).Trim().Split('|')
        $cudaAvail = if ($parts.Length -ge 1) { $parts[0] -eq "True" } else { $false }
        $gpuName = if ($parts.Length -ge 2) { $parts[1] } else { "None" }

        if ($cudaAvail) {
            Report-Item -Name "CUDA / GPU" -Status "PASS" -Detail "CUDA available: $gpuName"
        } else {
            Report-Item -Name "CUDA / GPU" -Status "WARN" -Detail "CUDA not available (PyTorch running on CPU)." -Guidance "Real mode will run on CPU, which is substantially slower for live inference."
        }
    } else {
        Report-Item -Name "CUDA / GPU" -Status "WARN" -Detail "Skipped because Python venv is missing."
    }

    # 4. LAN IPv4 Detection
    $resolvedLanIP = $null
    $selectedAdapterAlias = $null

    if ($LanIP) {
        $override = Resolve-ToolLightHouseLanIPv4 -ExplicitIP $LanIP
        if (-not $override.InvalidExplicit) {
            $resolvedLanIP = $override.IPAddress
            $selectedAdapterAlias = '(override)'
        }
    } elseif ($HostAddress -and $HostAddress -ne "0.0.0.0" -and $HostAddress -ne "localhost" -and $HostAddress -ne "127.0.0.1") {
        $resolvedLanIP = $HostAddress
    } elseif ($env:TOOLLIGHTHOUSE_LAN_IP) {
        $resolvedLanIP = $env:TOOLLIGHTHOUSE_LAN_IP
    } else {
        $lanResolution = Resolve-ToolLightHouseLanIPv4
        $resolvedLanIP = $lanResolution.IPAddress
        $selectedAdapterAlias = $lanResolution.InterfaceAlias
        if ($lanResolution.Ambiguous) {
            Report-Item -Name "LAN IPv4" -Status "WARN" -Detail "Ambiguous reachable addresses; no primary was guessed: $($lanResolution.Candidates -join ', ')" -Guidance "Re-run with -LanIP <IPv4> for the address reachable by tablet and phone."
        }
    }

    if ($LanIP -and -not $resolvedLanIP) {
        Report-Item -Name "LAN IPv4" -Status "FAIL" -Detail "-LanIP '$LanIP' is not a valid IPv4 address." -Guidance "Pass the IPv4 address reachable by tablet and phone."
    } elseif ($resolvedLanIP -and $resolvedLanIP -ne "127.0.0.1") {
        Report-Item -Name "LAN IPv4" -Status "PASS" -Detail "Reachable LAN IP: $resolvedLanIP $(if ($selectedAdapterAlias) { "($selectedAdapterAlias)" })"
    } else {
        Report-Item -Name "LAN IPv4" -Status "FAIL" -Detail "No non-virtual LAN IPv4 detected." -Guidance "Connect to local Wi-Fi or mobile hotspot so tablet and phone can reach the laptop, or pass -LanIP <IPv4>."
    }

    # 5. Compiled Frontend Build (Checked strictly in repository root)
    $distIndex = Join-Path $repoRoot "frontend\dist\index.html"
    $distAssets = Join-Path $repoRoot "frontend\dist\assets"

    if ((Test-Path $distIndex) -and (Test-Path $distAssets)) {
        Report-Item -Name "Frontend Build" -Status "PASS" -Detail "Production build present at $(Join-Path $repoRoot "frontend\dist")"
    } else {
        Report-Item -Name "Frontend Build" -Status "FAIL" -Detail "Compiled frontend missing (frontend/dist/index.html not found)." -Guidance "Compiled frontend is mandatory for demo operations. Run: cd frontend; npm run build"
    }

    # 6. Configuration, Inventory & Calibration Assets Validation via Settings
    $resolvedConfig = if ([System.IO.Path]::IsPathRooted($ConfigPath)) { $ConfigPath } else { Join-Path $repoRoot $ConfigPath }
    $configSettingsPy = @'
import sys, json
from pathlib import Path
repo_root = Path(sys.argv[1])
config_arg = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else ""
sys.path.insert(0, str(repo_root / "src"))

res = {"ok": False, "config_exists": False, "is_corrupt": False, "inventory_ok": False, "inventory_example": False, "inventory_detail": "", "assets": {}, "error": ""}
p_cfg = Path(config_arg) if config_arg else None

if p_cfg and p_cfg.exists():
    res["config_exists"] = True
    try:
        from toollighthouse.config import Settings
        settings = Settings.from_file(p_cfg)
    except Exception as e:
        res["is_corrupt"] = True
        res["error"] = f"Config file '{config_arg}' exists but failed to parse: {e}"
        print(json.dumps(res))
        sys.exit(0)
else:
    try:
        from toollighthouse.config import Settings
        settings = Settings()
    except Exception as e:
        res["error"] = f"Failed to instantiate default settings: {e}"
        print(json.dumps(res))
        sys.exit(0)

inv = settings.inventory
if inv is None or len(inv.categories) != 9:
    res["inventory_ok"] = False
    res["inventory_detail"] = f"Inventory categories count is {len(inv.categories) if inv else 0}, expected 9"
elif inv.example:
    res["inventory_ok"] = True
    res["inventory_example"] = True
    res["inventory_detail"] = "Inventory is using unconfirmed example template"
else:
    res["inventory_ok"] = True
    res["inventory_example"] = False
    res["inventory_detail"] = "Inventory has 9 confirmed categories"

res["assets"]["background_path"] = settings.background_path
res["assets"]["roi_path"] = settings.roi_path
res["assets"]["gallery_path"] = settings.gallery_path
res["assets"]["model_path"] = settings.model_path
res["ok"] = True
print(json.dumps(res))
'@

    if ($pythonExe) {
        $cfgRaw = $configSettingsPy | & $pythonExe - "$repoRoot" "$resolvedConfig" 2>$null
        $cfgData = $null
        try {
            $cfgData = $cfgRaw | ConvertFrom-Json
        } catch {}

        if ($cfgData -and $cfgData.is_corrupt) {
            Report-Item -Name "Configuration & Inventory" -Status "FAIL" -Detail $cfgData.error -Guidance "Actionable recovery: Check '$resolvedConfig.backup' or fix the JSON envelope syntax before running."
        } elseif ($cfgData -and $cfgData.ok) {
            if ($cfgData.inventory_example) {
                Report-Item -Name "Configuration & Inventory" -Status "WARN" -Detail "Inventory is using example template with unconfirmed locations." -Guidance "Expected for initial setup: Confirm tool pins D2-D10 in dashboard before driving physical LEDs."
            } elseif ($cfgData.inventory_ok) {
                Report-Item -Name "Configuration & Inventory" -Status "PASS" -Detail $cfgData.inventory_detail
            } else {
                Report-Item -Name "Configuration & Inventory" -Status "FAIL" -Detail $cfgData.inventory_detail -Guidance "Ensure inventory contains exactly 9 tool categories mapped to pins D2-D10."
            }

            # Check configured assets presence on disk
            $missingPrep = @()
            if ($cfgData.assets.background_path) {
                $p = if ([System.IO.Path]::IsPathRooted($cfgData.assets.background_path)) { $cfgData.assets.background_path } else { Join-Path $repoRoot $cfgData.assets.background_path }
                if (-not (Test-Path $p)) { $missingPrep += "empty-background ($($cfgData.assets.background_path))" }
            }
            if ($cfgData.assets.roi_path) {
                $p = if ([System.IO.Path]::IsPathRooted($cfgData.assets.roi_path)) { $cfgData.assets.roi_path } else { Join-Path $repoRoot $cfgData.assets.roi_path }
                if (-not (Test-Path $p)) { $missingPrep += "ROI ($($cfgData.assets.roi_path))" }
            }
            if ($cfgData.assets.gallery_path) {
                $p = if ([System.IO.Path]::IsPathRooted($cfgData.assets.gallery_path)) { $cfgData.assets.gallery_path } else { Join-Path $repoRoot $cfgData.assets.gallery_path }
                if (-not (Test-Path $p)) { $missingPrep += "gallery ($($cfgData.assets.gallery_path))" }
            }

            if ($missingPrep.Count -eq 0) {
                Report-Item -Name "Calibration Assets" -Status "PASS" -Detail "Configured background, ROI, and gallery files present on disk."
            } else {
                Report-Item -Name "Calibration Assets" -Status "WARN" -Detail "Missing preparation assets: $($missingPrep -join ', ')." -Guidance "Expected pre-demo preparation: In safe preparation mode, capture empty tray background, delimit ROI, and build gallery before scanning physical tools."
            }
        } else {
            Report-Item -Name "Configuration & Inventory" -Status "WARN" -Detail "Could not evaluate Settings: $cfgRaw"
        }
    } else {
        Report-Item -Name "Configuration & Inventory" -Status "WARN" -Detail "Skipped because Python venv is missing."
    }

    # 7. AI Model Presence (Reporting presence, not claiming unverified integrity)
    $modelDir = Join-Path $repoRoot "models\embeddinggemma-2"
    $modelWeights = Join-Path $modelDir "model.safetensors"
    $modelConfig = Join-Path $modelDir "config.json"

    if ((Test-Path $modelWeights) -and (Test-Path $modelConfig)) {
        $weightsSizeMB = [math]::Round((Get-Item $modelWeights).Length / 1MB, 1)
        Report-Item -Name "AI Model Presence" -Status "PASS" -Detail "embeddinggemma-2 model files present ($weightsSizeMB MB) at $modelDir"
    } else {
        Report-Item -Name "AI Model Presence" -Status "WARN" -Detail "embeddinggemma-2 model files not found at $modelDir" -Guidance "Expected for clean repository: Download model weights with: python -m toollighthouse download-model --model-path models/embeddinggemma-2"
    }

    # 8. TLS Certificate, Private Key Match & Exact SAN via demo_tls_check.py
    $resolvedCertDir = if ([System.IO.Path]::IsPathRooted($CertDir)) { $CertDir } else { Join-Path $repoRoot $CertDir }
    $certFile = Join-Path $resolvedCertDir "lan-cert.pem"
    $keyFile = Join-Path $resolvedCertDir "lan-key.pem"
    $tlsHelper = Join-Path $repoRoot "scripts\demo_tls_check.py"

    if ((Test-Path $certFile) -and (Test-Path $keyFile)) {
        $certData = $null
        if ($pythonExe) {
            $tlsArgs = @($tlsHelper, $certFile, $keyFile)
            if ($resolvedLanIP -and $resolvedLanIP -ne "127.0.0.1") {
                $tlsArgs += $resolvedLanIP
            }
            $certRaw = & $pythonExe @tlsArgs 2>$null
            try {
                $certData = $certRaw | ConvertFrom-Json
            } catch {}
        }

        if (-not $certData -or -not $certData.valid_dates) {
            $err = if ($certData -and $certData.error) { $certData.error } else { "Certificate expired or not yet valid." }
            Report-Item -Name "TLS Certificate" -Status "FAIL" -Detail $err -Guidance "Certificate expired or not yet valid. Regenerate: .\scripts\setup_certs.ps1 -Force"
        } elseif (-not $certData.key_match) {
            $err = if ($certData -and $certData.error) { $certData.error } else { "Certificate and private key pair mismatch or invalid." }
            Report-Item -Name "TLS Certificate" -Status "FAIL" -Detail $err -Guidance "Regenerate certificate and key pair: .\scripts\setup_certs.ps1 -Force"
        } elseif ($resolvedLanIP -and $resolvedLanIP -ne "127.0.0.1" -and (-not $certData.has_lan_ip)) {
            Report-Item -Name "TLS Certificate" -Status "FAIL" -Detail "Certificate SAN does not match current LAN IP '$resolvedLanIP' (SANs: $($certData.sans -join ', '))." -Guidance "Regenerate certificates for this IP: .\scripts\setup_certs.ps1 -Force"
        } else {
            Report-Item -Name "TLS Certificate" -Status "PASS" -Detail "Certificate and key match. Valid until $($certData.not_after). Exact SAN verified for '$resolvedLanIP'."
        }
    } else {
        Report-Item -Name "TLS Certificate" -Status "FAIL" -Detail "Missing lan-cert.pem or lan-key.pem in $resolvedCertDir" -Guidance "HTTPS is required for mobile camera access (getUserMedia). Run: .\scripts\setup_certs.ps1"
    }

    # 9. Port Availability and Duplicate Server Check
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

    $duplicateProcess = $null
    if (-not $env:TOOLLIGHTHOUSE_IGNORE_DUPLICATE_PROCESS) {
        try {
            $pyProcs = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue
            foreach ($p in $pyProcs) {
                if ($p.ProcessId -eq $PID) { continue }
                $cmd = $p.CommandLine
                if ($cmd -match '\btoollighthouse\b.*?\b(serve|run)\b' -or $cmd -match '\buvicorn\b.*?\btoollighthouse\b') {
                    $duplicateProcess = $p
                    break
                }
            }
        } catch {}
    }

    if ($portOccupied) {
        Report-Item -Name "Port Availability" -Status "FAIL" -Detail "Port $Port is already in use by PID $occupyingPid." -Guidance "Terminate the occupying process or select another port."
    } elseif ($duplicateProcess) {
        Report-Item -Name "Port Availability" -Status "FAIL" -Detail "Duplicate ToolLightHouse process detected (PID $($duplicateProcess.ProcessId))." -Guidance "Terminate running instance before starting demo to prevent GPU/serial port contention."
    } else {
        Report-Item -Name "Port Availability" -Status "PASS" -Detail "Port $Port is free and no duplicate project servers are running."
    }

    # 10. Windows Network Profile Check (Reporting selected interface profile)
    try {
        $selectedProfile = $null
        if ($selectedAdapterAlias) {
            $selectedProfile = Get-NetConnectionProfile -InterfaceAlias $selectedAdapterAlias -ErrorAction SilentlyContinue
        }
        if (-not $selectedProfile) {
            $selectedProfile = Get-NetConnectionProfile -ErrorAction SilentlyContinue | Select-Object -First 1
        }

        if ($selectedProfile) {
            $cat = $selectedProfile.NetworkCategory
            if ($cat -eq "Public") {
                Report-Item -Name "Network Profile" -Status "WARN" -Detail "Interface '$($selectedProfile.InterfaceAlias)' is set to 'Public'." -Guidance "Public networks do not universally block traffic if rules exist, but Windows Defender may restrict unsolicited inbound connections. Setting profile to 'Private' in Windows Network Settings is strongly recommended."
            } else {
                Report-Item -Name "Network Profile" -Status "PASS" -Detail "Interface '$($selectedProfile.InterfaceAlias)' profile is $cat."
            }
        } else {
            Report-Item -Name "Network Profile" -Status "WARN" -Detail "Could not identify active network connection profile."
        }
    } catch {
        Report-Item -Name "Network Profile" -Status "WARN" -Detail "Could not query network connection profile: $_"
    }

    # 11. Passive Windows Firewall Check for Port 8000 (Qualified)
    try {
        $fwPort = Get-NetFirewallPortFilter -ErrorAction SilentlyContinue | Where-Object {
            $_.LocalPort -eq "$Port" -or $_.LocalPort -eq $Port
        }
        $inboundRule = $null
        if ($fwPort) {
            $inboundRule = $fwPort | ForEach-Object { Get-NetFirewallRule -Name $_.InstanceID -ErrorAction SilentlyContinue } | Where-Object {
                $_.Direction -eq "Inbound" -and $_.Action -eq "Allow" -and $_.Enabled -eq "True"
            }
        }
        if ($inboundRule) {
            Report-Item -Name "Passive Firewall" -Status "PASS" -Detail "Passive inbound rule allowing TCP port $Port found (Note: passive rule does not guarantee Wi-Fi reachability; verify client isolation)."
        } else {
            Report-Item -Name "Passive Firewall" -Status "WARN" -Detail "No explicit inbound rule found for TCP port $Port." -Guidance "If tablet/phone cannot connect, run in Admin PowerShell:`n        New-NetFirewallRule -DisplayName `"ToolLightHouse LAN (HTTPS)`" -Direction Inbound -LocalPort $Port -Protocol TCP -Action Allow -Profile Private -RemoteAddress LocalSubnet"
        }
    } catch {
        Report-Item -Name "Passive Firewall" -Status "WARN" -Detail "Firewall rule check unavailable: $_"
    }

    # 12. Passive Serial (COM) Port Detection
    $comPorts = @()
    try {
        $comPorts = [System.IO.Ports.SerialPort]::GetPortNames()
    } catch {}
    if (-not $comPorts -or $comPorts.Count -eq 0) {
        try {
            $cimPorts = Get-CimInstance Win32_SerialPort -ErrorAction SilentlyContinue
            if ($cimPorts) {
                $comPorts = $cimPorts | ForEach-Object { $_.DeviceID }
            }
        } catch {}
    }

    if ($comPorts -and $comPorts.Count -gt 0) {
        Report-Item -Name "Arduino COM Ports" -Status "PASS" -Detail "Detected serial ports: $($comPorts -join ', '). Select appropriate port in dashboard Arduino tab."
    } else {
        Report-Item -Name "Arduino COM Ports" -Status "WARN" -Detail "No COM serial ports detected." -Guidance "Arduino Nano is not currently plugged in via USB. Connect hardware before physical LED actuation, or rehearse using simulated serial."
    }

    # Summary
    Write-Host "`n==========================================================" -ForegroundColor Cyan
    Write-Host " Preflight Summary: $passCount PASS, $warnCount WARN, $failCount FAIL" -ForegroundColor $(if ($failCount -gt 0) { "Red" } elseif ($warnCount -gt 0) { "Yellow" } else { "Green" })
    Write-Host "==========================================================" -ForegroundColor Cyan

    if ($failCount -gt 0) {
        Write-Host "[!] Preflight encountered $failCount blocker(s). Please resolve them before launching the demo.`n" -ForegroundColor Red
        exit 1
    } else {
        Write-Host "[+] All blocking checks passed. System is ready for demo launch.`n" -ForegroundColor Green
        exit 0
    }
} finally {
    if ($null -eq $priorPythonPath) {
        Remove-Item Env:\PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $priorPythonPath
    }
}
