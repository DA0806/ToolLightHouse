<#
.SYNOPSIS
    ToolLightHouse LAN HTTPS Certificate Generation Helper using mkcert.
.DESCRIPTION
    Generates local development and LAN certificates for ToolLightHouse multi-device
    operation (Laptop Server, Tablet Dashboard, and Phone Camera Transmitter).
    Creates certificates with Subject Alternative Names (SANs) for localhost,
    loopback IPs, and the primary active LAN IPv4 address.
.NOTES
    Does not automatically modify system trust stores or firewall rules.
    Generated .pem files are placed in certificates/ and are git-ignored.
    Public CA rootCA.pem should be copied only to authorized test devices.
    Private CA key rootCA-key.pem must NEVER be copied or committed.
#>

[CmdletBinding()]
param (
    [string]$CertDir = "certificates",
    [string]$AdditionalIP = "",
    [string[]]$Hostnames = @(),
    [switch]$Force = $false
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "network_helpers.ps1")

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " ToolLightHouse LAN HTTPS & Certificate Setup (mkcert)   " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Resolve paths relative to repository root
$repoRoot = Split-Path -Parent $PSScriptRoot
$resolvedCertDir = if ([System.IO.Path]::IsPathRooted($CertDir)) { $CertDir } else { Join-Path $repoRoot $CertDir }

if (-not (Test-Path $resolvedCertDir)) {
    New-Item -ItemType Directory -Path $resolvedCertDir -Force | Out-Null
    Write-Host "[+] Created directory: $resolvedCertDir" -ForegroundColor Green
}

# Verify .gitignore coverage
$gitignorePath = Join-Path $repoRoot ".gitignore"
if (Test-Path $gitignorePath) {
    $giContent = Get-Content $gitignorePath -Raw
    $folderName = [System.IO.Path]::GetFileName($resolvedCertDir.TrimEnd('\', '/'))
    if ($giContent -notmatch "(?m)^/?$([regex]::Escape($folderName))/?$") {
        Write-Warning "Directory '$folderName' may not be in .gitignore. Ensure certificate files are never committed."
    }
}

# 2. Check for mkcert binary
$mkcertCmd = Get-Command "mkcert" -ErrorAction SilentlyContinue
if (-not $mkcertCmd) {
    Write-Warning "mkcert was not found on your PATH."
    Write-Host "To install mkcert on Windows, run one of the following:" -ForegroundColor Yellow
    Write-Host "  winget install FiloSottile.mkcert" -ForegroundColor White
    Write-Host "  choco install mkcert" -ForegroundColor White
    Write-Host "Or download the binary from https://github.com/FiloSottile/mkcert/releases`n" -ForegroundColor White
    Write-Host "Once installed, rerun this script." -ForegroundColor Yellow
    exit 1
}

Write-Host "[+] Found mkcert at: $($mkcertCmd.Source)" -ForegroundColor Green

# 3. Inform user about Windows root store installation (manual instruction)
Write-Host "`n[i] Local Windows Trust Store:" -ForegroundColor Cyan
Write-Host "To trust certificates in your local Windows browsers, run manually once in an Administrator terminal:" -ForegroundColor White
Write-Host "  mkcert -install" -ForegroundColor Yellow
Write-Host "(This script does not automatically modify system trust stores without explicit consent).`n" -ForegroundColor DarkGray

$sanList = @("localhost", "127.0.0.1", "::1")
if ($AdditionalIP) {
    $lanResolution = Resolve-ToolLightHouseLanIPv4 -ExplicitIP $AdditionalIP
    if ($lanResolution.InvalidExplicit) {
        Write-Error "-AdditionalIP must be a valid IPv4 address reachable by the tablet and phone."
        exit 1
    }
    $lanIP = $AdditionalIP
    Write-Host "[+] Using explicit reachable LAN IPv4 override: $lanIP" -ForegroundColor Green
} else {
    $lanResolution = Resolve-ToolLightHouseLanIPv4
    $lanIP = $lanResolution.IPAddress
    if ($lanIP) {
        Write-Host "[+] Detected primary LAN IPv4: $lanIP ($($lanResolution.InterfaceAlias))" -ForegroundColor Green
    } elseif ($lanResolution.Ambiguous) {
        Write-Warning "Multiple reachable LAN IPv4 candidates found; no primary was guessed: $($lanResolution.Candidates -join ', ')"
        Write-Host "Specify the address reachable by the tablet and phone: .\scripts\setup_certs.ps1 -AdditionalIP <IPv4>" -ForegroundColor Yellow
    } else {
        Write-Warning "Could not automatically determine a reachable LAN IPv4 address."
        Write-Host "Specify the address manually: .\scripts\setup_certs.ps1 -AdditionalIP <IPv4>" -ForegroundColor Yellow
    }
}

if ($lanIP -and ($sanList -notcontains $lanIP)) {
    $sanList += $lanIP
}

if ($AdditionalIP) { Write-Host "[+] Added user-specified IP to certificate SANs: $AdditionalIP" -ForegroundColor Green }

foreach ($hostEntry in $Hostnames) {
    if ($hostEntry -and ($sanList -notcontains $hostEntry)) {
        $sanList += $hostEntry
        Write-Host "[+] Added user-specified hostname SAN: $hostEntry" -ForegroundColor Green
    }
}

Write-Host "`n[i] mDNS / Hostname Note:" -ForegroundColor Cyan
Write-Host "Android devices often do NOT resolve .local mDNS names without dedicated mDNS/Bonjour services." -ForegroundColor Yellow
Write-Host "For reliable tablet/phone connection, use the numeric LAN IPv4: https://$($lanIP):8000/" -ForegroundColor White

$certFile = Join-Path $resolvedCertDir "lan-cert.pem"
$keyFile = Join-Path $resolvedCertDir "lan-key.pem"

if ((Test-Path $certFile) -and (Test-Path $keyFile) -and (-not $Force)) {
    Write-Host "`n[i] Certificates already exist:" -ForegroundColor Yellow
    Write-Host "  Certificate: $certFile" -ForegroundColor White
    Write-Host "  Private Key: $keyFile" -ForegroundColor White
    Write-Host "[!] Skipping certificate generation to prevent silently overwriting existing keys." -ForegroundColor Yellow
    Write-Host "    If you changed network/IP and need to regenerate certificates, pass -Force:" -ForegroundColor Cyan
    Write-Host "    .\scripts\setup_certs.ps1 -Force`n" -ForegroundColor White
} else {
    Write-Host "`n[+] Generating certificates with SANs: $($sanList -join ', ')..." -ForegroundColor Cyan
    $sanArgs = @("-cert-file", $certFile, "-key-file", $keyFile) + $sanList
    try {
        & $mkcertCmd.Source @sanArgs
    } catch {
        Write-Host "[ERROR] Failed to execute mkcert: $_" -ForegroundColor Red
        exit 1
    }

    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to generate certificates with mkcert (Exit code: $LASTEXITCODE)." -ForegroundColor Red
        exit $LASTEXITCODE
    }

    Write-Host "[SUCCESS] Certificates created:" -ForegroundColor Green
    Write-Host "  Certificate: $certFile" -ForegroundColor White
    Write-Host "  Private Key: $keyFile" -ForegroundColor White
}

# 5. Root CA Export and Mobile Installation Instructions
$caRootOutput = try { & $mkcertCmd.Source -CAROOT } catch { $null }
if ($LASTEXITCODE -ne 0 -or -not $caRootOutput) {
    Write-Warning "Could not determine mkcert root CA path (-CAROOT failed)."
    $caRoot = "UNKNOWN"
    $caCertPath = "UNKNOWN/rootCA.pem"
    $caKeyPath = "UNKNOWN/rootCA-key.pem"
} else {
    $caRoot = ($caRootOutput | Out-String).Trim()
    $caCertPath = Join-Path $caRoot "rootCA.pem"
    $caKeyPath = Join-Path $caRoot "rootCA-key.pem"
}

Write-Host "`n----------------------------------------------------------" -ForegroundColor Cyan
Write-Host " Device Installation Instructions (Zero-Cloud Offline LAN)" -ForegroundColor Cyan
Write-Host "----------------------------------------------------------" -ForegroundColor Cyan
Write-Host "Root CA directory: $caRoot" -ForegroundColor Yellow
Write-Host "  Public CA Certificate: $caCertPath" -ForegroundColor Green
Write-Host "  PRIVATE CA KEY:        $caKeyPath (NEVER SHARE OR COPY!)" -ForegroundColor Red

Write-Host "`n[SECURITY NOTICE]:" -ForegroundColor Red
Write-Host "Copy ONLY rootCA.pem to authorized mobile devices." -ForegroundColor White
Write-Host "NEVER copy rootCA-key.pem to mobile devices, and NEVER commit it to Git!`n" -ForegroundColor White

Write-Host "To enable secure camera access (getUserMedia / WebRTC) on mobile devices," -ForegroundColor White
Write-Host "install the local public mkcert Root CA (rootCA.pem) on your phone / tablet once:`n" -ForegroundColor White

Write-Host "1. Android (Phone / Tablet):" -ForegroundColor Cyan
Write-Host "   a) Copy rootCA.pem to your device (via USB cable or local file transfer)." -ForegroundColor White
Write-Host "   b) Open Settings -> Security -> Encryption & credentials -> Install a certificate." -ForegroundColor White
Write-Host "   c) Select 'CA certificate', browse to rootCA.pem, and confirm installation." -ForegroundColor White

Write-Host "`n2. iOS / iPadOS:" -ForegroundColor Cyan
Write-Host "   a) AirDrop or download rootCA.pem onto your iPhone / iPad." -ForegroundColor White
Write-Host "   b) Open Settings -> Profile Downloaded -> Install." -ForegroundColor White
Write-Host "   c) IMPORTANT: Go to Settings -> General -> About -> Certificate Trust Settings." -ForegroundColor White
Write-Host "   d) Under 'Enable full trust for root certificates', turn ON the switch for mkcert root." -ForegroundColor White

Write-Host "`n3. Launching ToolLightHouse with HTTPS:" -ForegroundColor Cyan
Write-Host "   A. Demo Launcher (Recommended):" -ForegroundColor White
Write-Host "      .\scripts\start-demo.ps1" -ForegroundColor Green
Write-Host "   B. Real Mode (GPU + CUDA + Production):" -ForegroundColor White
Write-Host "      python -m toollighthouse serve --mode real --device cuda --ssl-certfile `"$certFile`" --ssl-keyfile `"$keyFile`"" -ForegroundColor Green
Write-Host "   C. Mock Mode (Development & Testing):" -ForegroundColor White
Write-Host "      python -m toollighthouse serve --mode mock --simulate-leds --ssl-certfile `"$certFile`" --ssl-keyfile `"$keyFile`"" -ForegroundColor Green

Write-Host "`n4. Windows Firewall Setup (Port 8000):" -ForegroundColor Cyan
Write-Host "   To allow inbound LAN connections from tablet and phone on your private network," -ForegroundColor White
Write-Host "   run in an Administrator PowerShell:" -ForegroundColor White
Write-Host "   New-NetFirewallRule -DisplayName `"ToolLightHouse LAN (HTTPS)`" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow -Profile Private -RemoteAddress LocalSubnet" -ForegroundColor Yellow
Write-Host "==========================================================`n" -ForegroundColor Cyan
