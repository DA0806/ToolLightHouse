<#
Shared LAN address selection for the Windows demo scripts.
The default route and adapter metadata are used instead of a subnet blacklist, so
host-only/virtual aliases cannot silently become the dashboard address.
#>

function Resolve-ToolLightHouseLanIPv4 {
    [CmdletBinding()]
    param (
        [string]$ExplicitIP = ""
    )

    if ($ExplicitIP) {
        $parsed = $null
        if (-not [System.Net.IPAddress]::TryParse($ExplicitIP, [ref]$parsed) -or $parsed.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
            return [pscustomobject]@{ IPAddress = $null; InterfaceAlias = $null; HasDefaultGateway = $false; Ambiguous = $false; InvalidExplicit = $true; Candidates = @() }
        }
        return [pscustomobject]@{ IPAddress = $ExplicitIP; InterfaceAlias = "(override)"; HasDefaultGateway = $false; Ambiguous = $false; InvalidExplicit = $false; Candidates = @($ExplicitIP) }
    }

    $adapterByAlias = @{}
    try {
        foreach ($adapter in @(Get-NetAdapter -ErrorAction Stop)) {
            $adapterByAlias[$adapter.Name] = $adapter
            if ($adapter.InterfaceAlias) { $adapterByAlias[$adapter.InterfaceAlias] = $adapter }
        }
    } catch {
        # Get-NetIPConfiguration still provides enough data when adapter metadata is unavailable.
    }

    $candidates = @()
    try {
        foreach ($config in @(Get-NetIPConfiguration -ErrorAction Stop)) {
            $adapter = $adapterByAlias[$config.InterfaceAlias]
            $isUp = $true
            if ($adapter -and $adapter.Status) { $isUp = ($adapter.Status -eq 'Up') }
            if (-not $isUp) { continue }

            $hardware = $true
            if ($adapter -and ($adapter.PSObject.Properties.Name -contains 'HardwareInterface')) {
                $hardware = [bool]$adapter.HardwareInterface
            } elseif ($adapter -and $adapter.Description) {
                # Metadata fallback only; no subnet or vendor-specific address blacklist.
                $hardware = $adapter.Description -notmatch '(?i)virtual|hyper-v|vmware|virtualbox|loopback|tunnel|wsl|tailscale|zerotier'
            }

            $hasGateway = [bool](@($config.IPv4DefaultGateway | Where-Object { $_.NextHop -and $_.NextHop -notmatch '^0\.0\.0\.0$' }).Count)
            foreach ($address in @($config.IPv4Address)) {
                $ip = if ($address -is [string]) { $address } else { $address.IPAddress }
                if (-not $ip -or $ip -match '^127\.|^169\.254\.') { continue }
                $candidates += [pscustomobject]@{
                    IPAddress = $ip
                    InterfaceAlias = $config.InterfaceAlias
                    HasDefaultGateway = $hasGateway
                    HardwareInterface = $hardware
                }
            }
        }
    } catch {
        return [pscustomobject]@{ IPAddress = $null; InterfaceAlias = $null; HasDefaultGateway = $false; Ambiguous = $false; InvalidExplicit = $false; Candidates = @() }
    }

    if (-not $candidates) {
        return [pscustomobject]@{ IPAddress = $null; InterfaceAlias = $null; HasDefaultGateway = $false; Ambiguous = $false; InvalidExplicit = $false; Candidates = @() }
    }

    # One default-route interface is authoritative. Multiple routes are ambiguous.
    $gatewayCandidates = @($candidates | Where-Object { $_.HasDefaultGateway -and $_.HardwareInterface })
    if ($gatewayCandidates.Count -eq 1) {
        $selected = $gatewayCandidates[0]
        return [pscustomobject]@{ IPAddress = $selected.IPAddress; InterfaceAlias = $selected.InterfaceAlias; HasDefaultGateway = $true; Ambiguous = $false; InvalidExplicit = $false; Candidates = @($candidates | ForEach-Object { $_.IPAddress }) }
    }
    if ($gatewayCandidates.Count -gt 1) {
        return [pscustomobject]@{ IPAddress = $null; InterfaceAlias = $null; HasDefaultGateway = $false; Ambiguous = $true; InvalidExplicit = $false; Candidates = @($gatewayCandidates | ForEach-Object { "$($_.IPAddress) ($($_.InterfaceAlias), gateway)" }) }
    }

    # Hotspots can have no default gateway. A single usable physical address is safe;
    # multiple candidates require an explicit -LanIP so we never print a guessed URL.
    $physicalCandidates = @($candidates | Where-Object { $_.HardwareInterface })
    if ($physicalCandidates.Count -eq 1) {
        $selected = $physicalCandidates[0]
        return [pscustomobject]@{ IPAddress = $selected.IPAddress; InterfaceAlias = $selected.InterfaceAlias; HasDefaultGateway = $false; Ambiguous = $false; InvalidExplicit = $false; Candidates = @($candidates | ForEach-Object { $_.IPAddress }) }
    }
    return [pscustomobject]@{ IPAddress = $null; InterfaceAlias = $null; HasDefaultGateway = $false; Ambiguous = $true; InvalidExplicit = $false; Candidates = @($physicalCandidates | ForEach-Object { "$($_.IPAddress) ($($_.InterfaceAlias), sin gateway)" }) }
}
