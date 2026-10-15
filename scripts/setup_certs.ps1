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
    [string[]]$Hostnames = @()
)

$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " ToolLightHouse LAN HTTPS & Certificate Setup (mkcert)   " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Verify or create certificate directory
if (-not (Test-Path $CertDir)) {
    New-Item -ItemType Directory -Path $CertDir -Force | Out-Null
    Write-Host "[+] Created directory: $CertDir" -ForegroundColor Green
}

# Verify .gitignore coverage
$gitignorePath = ".gitignore"
if (Test-Path $gitignorePath) {
    $giContent = Get-Content $gitignorePath -Raw
    if ($giContent -notmatch "(?m)^/?$CertDir/?$") {
        Write-Warning "Directory '$CertDir' may not be in .gitignore. Ensure certificate files are never committed."
    }
}

# 2. Check for mkcert binary
$mkcert = Get-Command "mkcert" -ErrorAction SilentlyContinue
if (-not $mkcert) {
    Write-Warning "mkcert was not found on your PATH."
    Write-Host "To install mkcert on Windows, run one of the following:" -ForegroundColor Yellow
    Write-Host "  winget install FiloSottile.mkcert" -ForegroundColor White
    Write-Host "  choco install mkcert" -ForegroundColor White
    Write-Host "Or download the binary from https://github.com/FiloSottile/mkcert/releases`n" -ForegroundColor White
    Write-Host "Once installed, rerun this script." -ForegroundColor Yellow
    exit 1
}

Write-Host "[+] Found mkcert at: $($mkcert.Source)" -ForegroundColor Green

# 3. Inform user about Windows root store installation (manual instruction)
Write-Host "`n[i] Local Windows Trust Store:" -ForegroundColor Cyan
Write-Host "To trust certificates in your local Windows browsers, run manually once in an Administrator terminal:" -ForegroundColor White
Write-Host "  mkcert -install" -ForegroundColor Yellow
Write-Host "(This script does not automatically modify system trust stores without explicit consent).`n" -ForegroundColor DarkGray

# 4. Detect primary LAN IPv4 address
$lanIP = $null
try {
    $activeAdapters = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {
        $_.InterfaceAlias -notmatch 'Loopback|vEthernet|WSL|Tailscale|ZeroTier|Virtual' -and
        $_.IPAddress -notmatch '^127\.|^169\.254\.'
    }
    if ($activeAdapters) {
        $lanIP = ($activeAdapters | Select-Object -First 1).IPAddress
    }
} catch {
    Write-Verbose "Could not detect IP via Get-NetIPAddress, trying fallback..."
}

if (-not $lanIP) {
    # Fallback to ipconfig parse
    $ipconfig = ipconfig | Out-String
    if ($ipconfig -match 'IPv4 Address[ .:]+([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)') {
        $lanIP = $matches[1]
    }
}

$sanList = @("localhost", "127.0.0.1", "::1")
if ($lanIP) {
    Write-Host "[+] Detected primary LAN IPv4: $lanIP" -ForegroundColor Green
    $sanList += $lanIP
} else {
    Write-Warning "Could not automatically determine primary LAN IP."
    Write-Host "You can specify an IP manually: .\scripts\setup_certs.ps1 -AdditionalIP 192.168.1.X" -ForegroundColor Yellow
}

if ($AdditionalIP -and ($sanList -notcontains $AdditionalIP)) {
    $sanList += $AdditionalIP
    Write-Host "[+] Added user-specified IP: $AdditionalIP" -ForegroundColor Green
}

foreach ($hostEntry in $Hostnames) {
    if ($hostEntry -and ($sanList -notcontains $hostEntry)) {
        $sanList += $hostEntry
        Write-Host "[+] Added user-specified hostname SAN: $hostEntry" -ForegroundColor Green
    }
}

Write-Host "`n[i] mDNS / Hostname Note:" -ForegroundColor Cyan
Write-Host "Android devices often do NOT resolve .local mDNS names without dedicated mDNS/Bonjour services." -ForegroundColor Yellow
Write-Host "For reliable tablet/phone connection, use the numeric LAN IPv4: https://$($lanIP):8000/" -ForegroundColor White

$certFile = Join-Path $CertDir "lan-cert.pem"
$keyFile = Join-Path $CertDir "lan-key.pem"

Write-Host "`n[+] Generating certificates with SANs: $($sanList -join ', ')..." -ForegroundColor Cyan
& mkcert -cert-file $certFile -key-file $keyFile $sanList

if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to generate certificates with mkcert (Exit code: $LASTEXITCODE)."
    exit $LASTEXITCODE
}

Write-Host "[SUCCESS] Certificates created:" -ForegroundColor Green
Write-Host "  Certificate: $certFile" -ForegroundColor White
Write-Host "  Private Key: $keyFile" -ForegroundColor White

# 5. Root CA Export and Mobile Installation Instructions
$caRoot = (& mkcert -CAROOT).Trim()
$caCertPath = Join-Path $caRoot "rootCA.pem"
$caKeyPath = Join-Path $caRoot "rootCA-key.pem"

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
Write-Host "   A. Real Mode (GPU + CUDA + Production):" -ForegroundColor White
Write-Host "      python -m toollighthouse serve --mode real --device cuda --ssl-certfile $certFile --ssl-keyfile $keyFile" -ForegroundColor Green
Write-Host "   B. Mock Mode (Development & Testing):" -ForegroundColor White
Write-Host "      python -m toollighthouse serve --mode mock --simulate-leds --ssl-certfile $certFile --ssl-keyfile $keyFile" -ForegroundColor Green
Write-Host "   C. Direct Uvicorn Factory Alternative:" -ForegroundColor White
Write-Host "      python -m uvicorn toollighthouse.api:create_app --factory --host 0.0.0.0 --port 8000 --ssl-certfile $certFile --ssl-keyfile $keyFile" -ForegroundColor DarkGray

Write-Host "`n4. Windows Firewall Setup (Port 8000):" -ForegroundColor Cyan
Write-Host "   To allow inbound LAN connections from tablet and phone on your private network," -ForegroundColor White
Write-Host "   run in an Administrator PowerShell:" -ForegroundColor White
Write-Host "   New-NetFirewallRule -DisplayName `"ToolLightHouse LAN (HTTPS)`" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow -Profile Private -RemoteAddress LocalSubnet" -ForegroundColor Yellow
Write-Host "==========================================================`n" -ForegroundColor Cyan
