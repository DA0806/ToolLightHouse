import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.demo_tls_check import validate_cert_pair


# --- Runtime OpenSSL Certificate Fixture Generator ---
def find_openssl() -> str | None:
    """Locate openssl executable on system or git installation."""
    candidate = shutil.which("openssl")
    if candidate:
        return candidate
    for p in [
        r"C:\Program Files\Git\usr\bin\openssl.exe",
        r"C:\Program Files (x86)\Git\usr\bin\openssl.exe",
    ]:
        if Path(p).exists():
            return p
    return None


def generate_test_certs(
    output_dir: Path,
    sans: list[str] | None = None,
    days: int = 365,
) -> tuple[Path, Path, Path]:
    """Generate temporary test certificate, matching key, and wrong key at runtime via OpenSSL.
    
    Skips the test if OpenSSL is not available. Does not touch user certificate stores.
    """
    openssl = find_openssl()
    if not openssl:
        pytest.skip("OpenSSL executable not found for runtime certificate generation")

    output_dir.mkdir(parents=True, exist_ok=True)
    cert_path = output_dir / "lan-cert.pem"
    key_path = output_dir / "lan-key.pem"
    wrong_key_path = output_dir / "wrong-key.pem"

    if sans is None:
        sans = ["DNS:localhost", "IP:127.0.0.1", "IP:192.168.0.11"]
    san_ext = f"subjectAltName={','.join(sans)}"

    # Generate matching cert + private key
    cmd_pair = [
        openssl,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-keyout",
        str(key_path),
        "-out",
        str(cert_path),
        "-days",
        str(days),
        "-subj",
        "/CN=localhost",
        "-addext",
        san_ext,
    ]
    res_pair = subprocess.run(cmd_pair, capture_output=True, text=True)
    assert res_pair.returncode == 0, f"OpenSSL cert generation failed: {res_pair.stderr}"

    # Generate an independent mismatched private key
    cmd_wrong = [openssl, "genrsa", "-out", str(wrong_key_path), "2048"]
    res_wrong = subprocess.run(cmd_wrong, capture_output=True, text=True)
    assert res_wrong.returncode == 0, f"OpenSSL wrong key generation failed: {res_wrong.stderr}"

    return cert_path, key_path, wrong_key_path


# ==============================================================================
# 1. PORTABLE TESTS (Run on all platforms, including Linux CI)
# ==============================================================================

def test_spanish_and_english_ipconfig_regex_matching():
    """Verify regex matches both English and Spanish ipconfig IPv4 lines."""
    pattern = re.compile(r"(?i)(?:IPv4 Address|Direcci[oó\u00f3]n IPv4)[ .:]+([0-9]+\.[0-9]+\.[0-9]+\.[0-9]+)")

    english_line = "   IPv4 Address. . . . . . . . . . . : 192.168.1.100"
    spanish_line = "   Dirección IPv4. . . . . . . . . . . . . . : 192.168.0.11"
    spanish_ascii_line = "   Direccion IPv4. . . . . . . . . . . . . . : 10.0.0.5"

    m_en = pattern.search(english_line)
    assert m_en is not None
    assert m_en.group(1) == "192.168.1.100"

    m_es = pattern.search(spanish_line)
    assert m_es is not None
    assert m_es.group(1) == "192.168.0.11"

    m_es_ascii = pattern.search(spanish_ascii_line)
    assert m_es_ascii is not None
    assert m_es_ascii.group(1) == "10.0.0.5"


def test_config_envelope_parsing(tmp_path):
    """Verify that settings can be loaded from both standard envelope and raw dictionaries."""
    from toollighthouse.config import Settings

    envelope_file = tmp_path / "envelope.json"
    envelope_data = {
        "schema_version": 1,
        "config_version": 3,
        "settings": {
            "mode": "mock",
            "classifier_threshold": 0.85,
        },
        "inventory": {
            "example": False,
            "categories": [
                {
                    "class_id": f"tool_{i+1}",
                    "name": f"Tool {i+1}",
                    "location": f"slot_{i+1}",
                    "led_index": i,
                    "pin": i + 2,
                }
                for i in range(9)
            ],
        },
    }
    envelope_file.write_text(json.dumps(envelope_data), encoding="utf-8")

    settings = Settings.from_file(envelope_file)
    assert settings.mode == "mock"
    assert settings.classifier_threshold == 0.85
    assert len(settings.inventory.categories) == 9
    assert settings.inventory.categories[0].class_id == "tool_1"


def test_corrupt_config_fails_closed(tmp_path):
    """Verify that corrupt or invalid configuration fails closed rather than proceeding silently."""
    from toollighthouse.config import Settings

    corrupt_file = tmp_path / "corrupt_config.json"
    corrupt_file.write_text("{ unclosed json: invalid", encoding="utf-8")

    with pytest.raises(Exception):
        Settings.from_file(corrupt_file)


def test_cert_pair_validation_success(tmp_path):
    """Verify that a matching valid certificate and key pair passes validation."""
    cert_p, key_p, _ = generate_test_certs(tmp_path)

    res = validate_cert_pair(cert_p, key_p, lan_ip="192.168.0.11")
    assert res["ok"] is True
    assert res["key_match"] is True
    assert res["valid_dates"] is True
    assert res["has_lan_ip"] is True
    assert res["has_localhost_dns"] is True
    assert res["has_localhost_ip"] is True


def test_cert_pair_validation_mismatched_key(tmp_path):
    """Verify that a key from an unrelated certificate is rejected by load_cert_chain."""
    cert_p, _, wrong_key_p = generate_test_certs(tmp_path)

    res = validate_cert_pair(cert_p, wrong_key_p, lan_ip="192.168.0.11")
    assert res["ok"] is False
    assert res["key_match"] is False
    assert "mismatch" in res["error"].lower() or "key" in res["error"].lower()


def test_exact_ip_san_matching_rejects_prefix_and_suffix(tmp_path):
    """Verify exact IP matching in SANs; prefix substring '192.168.0.1' MUST be rejected when SAN is '192.168.0.11'."""
    cert_p, key_p, _ = generate_test_certs(tmp_path, sans=["DNS:localhost", "IP:127.0.0.1", "IP:192.168.0.11"])

    # Exact match succeeds
    res_exact = validate_cert_pair(cert_p, key_p, lan_ip="192.168.0.11")
    assert res_exact["has_lan_ip"] is True

    # Prefix substring match MUST fail
    res_prefix = validate_cert_pair(cert_p, key_p, lan_ip="192.168.0.1")
    assert res_prefix["has_lan_ip"] is False

    # Suffix substring match MUST fail
    res_suffix = validate_cert_pair(cert_p, key_p, lan_ip="168.0.11")
    assert res_suffix["has_lan_ip"] is False

    # Unrelated IP MUST fail
    res_other = validate_cert_pair(cert_p, key_p, lan_ip="192.168.0.12")
    assert res_other["has_lan_ip"] is False


def test_expired_and_not_yet_valid_dates(tmp_path):
    """Verify that expired and not-yet-valid certificates are detected and rejected using injectable timestamp."""
    cert_p, key_p, _ = generate_test_certs(tmp_path, days=365)

    # Simulated future date beyond 365 days
    future_ts = time.time() + (400 * 86400)
    res_future = validate_cert_pair(cert_p, key_p, lan_ip="192.168.0.11", now_ts=future_ts)
    assert res_future["ok"] is False
    assert res_future["valid_dates"] is False
    assert "expired" in res_future["error"].lower()

    # Simulated past date before issuance
    past_ts = time.time() - (400 * 86400)
    res_past = validate_cert_pair(cert_p, key_p, lan_ip="192.168.0.11", now_ts=past_ts)
    assert res_past["ok"] is False
    assert res_past["valid_dates"] is False
    assert "not yet valid" in res_past["error"].lower()


# ==============================================================================
# 2. WINDOWS POWERSHELL TESTS (Guarded for Windows CI/local host)
# ==============================================================================

IS_WINDOWS = sys.platform == "win32" and shutil.which("powershell.exe") is not None
windows_only = pytest.mark.skipif(not IS_WINDOWS, reason="PowerShell tests require Windows with powershell.exe")


def _run_ps_command(cmd: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    py_exec = sys.executable.replace("\\", "/")
    full_cmd = (
        f"$env:PYTHON_EXECUTABLE = '{py_exec}'; "
        f"$env:TOOLLIGHTHOUSE_IGNORE_DUPLICATE_PROCESS = '1'; "
        f"$env:TOOLLIGHTHOUSE_LAN_IP = '192.168.0.11'; "
        f"{cmd}"
    )
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", full_cmd],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
    )


@windows_only
def test_scripts_parse_cleanly_in_powershell_ast():
    """Verify that all operational scripts parse with zero syntax errors in PowerShell 5.1 AST."""
    scripts = [
        REPO_ROOT / "scripts" / "setup_certs.ps1",
        REPO_ROOT / "scripts" / "preflight.ps1",
        REPO_ROOT / "scripts" / "start-demo.ps1",
    ]
    for script in scripts:
        assert script.exists(), f"Script missing: {script}"
        ps_cmd = (
            f"$errors = $null; "
            f"$null = [System.Management.Automation.Language.Parser]::ParseFile('{script}', [ref]$null, [ref]$errors); "
            f"if ($errors.Count -gt 0) {{ $errors | ForEach-Object {{ $_.Message }}; exit $errors.Count }} else {{ exit 0 }}"
        )
        res = _run_ps_command(ps_cmd)
        assert res.returncode == 0, f"Syntax errors in {script.name}:\n{res.stdout}\n{res.stderr}"


@windows_only
def test_preflight_missing_certs_in_isolated_fixture(tmp_path):
    """Verify preflight.ps1 reports FAIL and exit code 1 when certs are missing in an isolated fixture."""
    fixture_dir = tmp_path / "project_fixture"
    fixture_dir.mkdir()
    (fixture_dir / "scripts").mkdir()
    (fixture_dir / "frontend" / "dist").mkdir(parents=True)
    (fixture_dir / "frontend" / "dist" / "index.html").write_text("stub", encoding="utf-8")
    (fixture_dir / "frontend" / "dist" / "assets").mkdir()
    (fixture_dir / "certificates").mkdir()

    shutil.copy(REPO_ROOT / "scripts" / "preflight.ps1", fixture_dir / "scripts" / "preflight.ps1")
    shutil.copy(REPO_ROOT / "scripts" / "network_helpers.ps1", fixture_dir / "scripts" / "network_helpers.ps1")
    shutil.copy(REPO_ROOT / "scripts" / "demo_tls_check.py", fixture_dir / "scripts" / "demo_tls_check.py")

    res = _run_ps_command("& '.\\scripts\\preflight.ps1'", cwd=fixture_dir)
    assert res.returncode == 1
    assert "TLS Certificate" in res.stdout
    assert "Missing lan-cert.pem or lan-key.pem" in res.stdout


@windows_only
def test_start_demo_rejects_occupied_port_in_isolated_fixture(tmp_path):
    """Verify start-demo.ps1 rejects startup when designated port is occupied, in an isolated fixture."""
    fixture_dir = tmp_path / "project_fixture"
    fixture_dir.mkdir()
    (fixture_dir / "scripts").mkdir()
    (fixture_dir / "frontend" / "dist").mkdir(parents=True)
    (fixture_dir / "frontend" / "dist" / "index.html").write_text("stub", encoding="utf-8")

    generate_test_certs(fixture_dir / "certificates")
    shutil.copy(REPO_ROOT / "scripts" / "start-demo.ps1", fixture_dir / "scripts" / "start-demo.ps1")
    shutil.copy(REPO_ROOT / "scripts" / "network_helpers.ps1", fixture_dir / "scripts" / "network_helpers.ps1")
    shutil.copy(REPO_ROOT / "scripts" / "demo_tls_check.py", fixture_dir / "scripts" / "demo_tls_check.py")

    # Bind temporary listening port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]

    try:
        ps_cmd = (
            f"$env:TOOLLIGHTHOUSE_PAIRING_KEY = 'test_pair'; "
            f"$env:TOOLLIGHTHOUSE_ADMIN_KEY = 'test_admin'; "
            f"& '.\\scripts\\start-demo.ps1' -Port {port} -NonInteractive"
        )
        res = _run_ps_command(ps_cmd, cwd=fixture_dir)
        assert res.returncode == 1
        assert f"Port {port} is already occupied" in res.stdout
    finally:
        sock.close()


@windows_only
def test_start_demo_fails_closed_on_corrupt_config(tmp_path):
    """Verify start-demo.ps1 fails closed with actionable recovery when config is corrupt JSON."""
    fixture_dir = tmp_path / "project_fixture"
    fixture_dir.mkdir()
    (fixture_dir / "scripts").mkdir()
    (fixture_dir / "frontend" / "dist").mkdir(parents=True)
    (fixture_dir / "frontend" / "dist" / "index.html").write_text("stub", encoding="utf-8")

    generate_test_certs(fixture_dir / "certificates")
    (fixture_dir / "config").mkdir()
    corrupt_cfg = fixture_dir / "config" / "app_config.json"
    corrupt_cfg.write_text("{ corrupt json: ", encoding="utf-8")

    shutil.copy(REPO_ROOT / "scripts" / "start-demo.ps1", fixture_dir / "scripts" / "start-demo.ps1")
    shutil.copy(REPO_ROOT / "scripts" / "network_helpers.ps1", fixture_dir / "scripts" / "network_helpers.ps1")
    shutil.copy(REPO_ROOT / "scripts" / "demo_tls_check.py", fixture_dir / "scripts" / "demo_tls_check.py")

    ps_cmd = (
        "$env:TOOLLIGHTHOUSE_PAIRING_KEY = 'test_pair'; "
        "$env:TOOLLIGHTHOUSE_ADMIN_KEY = 'test_admin'; "
        "& '.\\scripts\\start-demo.ps1' -NonInteractive -DryRun"
    )
    res = _run_ps_command(ps_cmd, cwd=fixture_dir)
    assert res.returncode == 1
    assert "corrupt or unreadable" in res.stdout.lower() or "not a valid configuration envelope" in res.stdout.lower()
    assert "Actionable recovery" in res.stdout


@windows_only
def test_start_demo_fails_closed_on_invalid_settings_before_prompt(tmp_path):
    """Verify start-demo.ps1 validates settings semantically and fails closed before prompting."""
    fixture_dir = tmp_path / "project_fixture"
    fixture_dir.mkdir()
    (fixture_dir / "scripts").mkdir()
    (fixture_dir / "src").mkdir()
    (fixture_dir / "frontend" / "dist").mkdir(parents=True)
    (fixture_dir / "frontend" / "dist" / "index.html").write_text("stub", encoding="utf-8")

    generate_test_certs(fixture_dir / "certificates")
    (fixture_dir / "config").mkdir()
    bad_cfg = fixture_dir / "config" / "app_config.json"
    # Valid envelope syntax but invalid setting value
    bad_cfg.write_text(json.dumps({
        "schema_version": 1,
        "settings": {
            "mode": "invalid_mode_never_supported"
        }
    }), encoding="utf-8")

    shutil.copy(REPO_ROOT / "scripts" / "start-demo.ps1", fixture_dir / "scripts" / "start-demo.ps1")
    shutil.copy(REPO_ROOT / "scripts" / "network_helpers.ps1", fixture_dir / "scripts" / "network_helpers.ps1")
    shutil.copy(REPO_ROOT / "scripts" / "demo_tls_check.py", fixture_dir / "scripts" / "demo_tls_check.py")

    ps_cmd = (
        "$env:TOOLLIGHTHOUSE_PAIRING_KEY = 'test_pair'; "
        "$env:TOOLLIGHTHOUSE_ADMIN_KEY = 'test_admin'; "
        "& '.\\scripts\\start-demo.ps1' -NonInteractive -DryRun"
    )
    res = _run_ps_command(ps_cmd, cwd=fixture_dir)
    assert res.returncode == 1
    assert "contains invalid settings" in res.stdout or "failed semantic validation" in res.stdout
    assert "Actionable recovery" in res.stdout


@windows_only
def test_start_demo_requires_admin_key_in_noninteractive(tmp_path):
    """Verify start-demo.ps1 requires nonempty admin key in NonInteractive mode for demo operations."""
    fixture_dir = tmp_path / "project_fixture"
    fixture_dir.mkdir()
    (fixture_dir / "scripts").mkdir()
    (fixture_dir / "frontend" / "dist").mkdir(parents=True)
    (fixture_dir / "frontend" / "dist" / "index.html").write_text("stub", encoding="utf-8")

    generate_test_certs(fixture_dir / "certificates")
    shutil.copy(REPO_ROOT / "scripts" / "start-demo.ps1", fixture_dir / "scripts" / "start-demo.ps1")
    shutil.copy(REPO_ROOT / "scripts" / "network_helpers.ps1", fixture_dir / "scripts" / "network_helpers.ps1")
    shutil.copy(REPO_ROOT / "scripts" / "demo_tls_check.py", fixture_dir / "scripts" / "demo_tls_check.py")

    ps_cmd = (
        "$env:TOOLLIGHTHOUSE_PAIRING_KEY = 'test_pair'; "
        "$env:TOOLLIGHTHOUSE_ADMIN_KEY = ''; "
        "& '.\\scripts\\start-demo.ps1' -NonInteractive -DryRun"
    )
    res = _run_ps_command(ps_cmd, cwd=fixture_dir)
    assert res.returncode == 1
    assert "TOOLLIGHTHOUSE_ADMIN_KEY is required for demo operations" in res.stdout


@windows_only
def test_start_demo_fails_closed_when_no_lan_ip_available(tmp_path):
    """Verify start-demo.ps1 fails closed when no valid LAN IP is detected or 127.0.0.1 is forced."""
    fixture_dir = tmp_path / "project_fixture"
    fixture_dir.mkdir()
    (fixture_dir / "scripts").mkdir()
    (fixture_dir / "frontend" / "dist").mkdir(parents=True)
    (fixture_dir / "frontend" / "dist" / "index.html").write_text("stub", encoding="utf-8")

    generate_test_certs(fixture_dir / "certificates")
    shutil.copy(REPO_ROOT / "scripts" / "start-demo.ps1", fixture_dir / "scripts" / "start-demo.ps1")
    shutil.copy(REPO_ROOT / "scripts" / "network_helpers.ps1", fixture_dir / "scripts" / "network_helpers.ps1")
    shutil.copy(REPO_ROOT / "scripts" / "demo_tls_check.py", fixture_dir / "scripts" / "demo_tls_check.py")

    ps_cmd = (
        "$env:TOOLLIGHTHOUSE_PAIRING_KEY = 'test_pair'; "
        "$env:TOOLLIGHTHOUSE_ADMIN_KEY = 'test_admin'; "
        "& '.\\scripts\\start-demo.ps1' -LanIP '127.0.0.1' -NonInteractive -DryRun"
    )
    res = _run_ps_command(ps_cmd, cwd=fixture_dir)
    assert res.returncode == 1
    assert "No non-virtual LAN IPv4 detected" in res.stdout


@windows_only
def test_start_demo_credentials_and_pythonpath_cleanup(tmp_path):
    """Verify that start-demo.ps1 restores both credentials and PYTHONPATH in its finally block."""
    fixture_dir = tmp_path / "project_fixture"
    fixture_dir.mkdir()
    (fixture_dir / "scripts").mkdir()
    (fixture_dir / "frontend" / "dist").mkdir(parents=True)
    (fixture_dir / "frontend" / "dist" / "index.html").write_text("stub", encoding="utf-8")

    generate_test_certs(fixture_dir / "certificates")
    shutil.copy(REPO_ROOT / "scripts" / "start-demo.ps1", fixture_dir / "scripts" / "start-demo.ps1")
    shutil.copy(REPO_ROOT / "scripts" / "network_helpers.ps1", fixture_dir / "scripts" / "network_helpers.ps1")
    shutil.copy(REPO_ROOT / "scripts" / "demo_tls_check.py", fixture_dir / "scripts" / "demo_tls_check.py")

    test_cmd = (
        "$env:TOOLLIGHTHOUSE_PAIRING_KEY = 'initial_pair'; "
        "$env:TOOLLIGHTHOUSE_ADMIN_KEY = 'initial_admin'; "
        "$env:PYTHONPATH = 'initial_pypath'; "
        "try { "
        "    & '.\\scripts\\start-demo.ps1' -Port 99999 -NonInteractive -DryRun 2>$null; "
        "} catch {} "
        "Write-Output \"PAIRING=$($env:TOOLLIGHTHOUSE_PAIRING_KEY)\"; "
        "Write-Output \"ADMIN=$($env:TOOLLIGHTHOUSE_ADMIN_KEY)\"; "
        "Write-Output \"PYTHONPATH=$($env:PYTHONPATH)\""
    )
    res = _run_ps_command(test_cmd, cwd=fixture_dir)
    assert "PAIRING=initial_pair" in res.stdout
    assert "ADMIN=initial_admin" in res.stdout
    assert "PYTHONPATH=initial_pypath" in res.stdout


@windows_only
def test_lan_resolver_prefers_physical_default_route_over_virtual_alias(tmp_path):
    """A host-only alias must not win when a physical adapter owns the default route."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "network_helpers.ps1", scripts_dir / "network_helpers.ps1")
    ps_cmd = r"""
function Get-NetAdapter {
    [pscustomobject]@{ Name = 'Ethernet'; InterfaceAlias = 'Ethernet'; Status = 'Up'; HardwareInterface = $true; Description = 'Real NIC' }
    [pscustomobject]@{ Name = 'VirtualBox Host-Only'; InterfaceAlias = 'VirtualBox Host-Only'; Status = 'Up'; HardwareInterface = $false; Description = 'Virtual adapter' }
}
function Get-NetIPConfiguration {
    [pscustomobject]@{ InterfaceAlias = 'VirtualBox Host-Only'; IPv4Address = @([pscustomobject]@{ IPAddress = '192.168.56.1' }); IPv4DefaultGateway = @() }
    [pscustomobject]@{ InterfaceAlias = 'Ethernet'; IPv4Address = @([pscustomobject]@{ IPAddress = '10.10.0.20' }); IPv4DefaultGateway = @([pscustomobject]@{ NextHop = '10.10.0.1' }) }
}
. '.\scripts\network_helpers.ps1'
$r = Resolve-ToolLightHouseLanIPv4
Write-Output "$($r.IPAddress)|$($r.InterfaceAlias)|$($r.Ambiguous)"
"""
    res = _run_ps_command(ps_cmd, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "10.10.0.20|Ethernet|False" in res.stdout


@windows_only
def test_lan_resolver_accepts_explicit_hotspot_without_gateway(tmp_path):
    """A hotspot address is an explicit operator choice when route metadata is absent."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "network_helpers.ps1", scripts_dir / "network_helpers.ps1")
    ps_cmd = r"""
. '.\scripts\network_helpers.ps1'
$r = Resolve-ToolLightHouseLanIPv4 -ExplicitIP '192.168.137.1'
Write-Output "$($r.IPAddress)|$($r.InterfaceAlias)|$($r.InvalidExplicit)"
"""
    res = _run_ps_command(ps_cmd, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "192.168.137.1|(override)|False" in res.stdout


@windows_only
def test_lan_resolver_marks_multiple_no_gateway_addresses_ambiguous(tmp_path):
    """Without a default route, multiple physical addresses require an explicit choice."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    shutil.copy(REPO_ROOT / "scripts" / "network_helpers.ps1", scripts_dir / "network_helpers.ps1")
    ps_cmd = r"""
function Get-NetAdapter {
    [pscustomobject]@{ Name = 'Wi-Fi'; InterfaceAlias = 'Wi-Fi'; Status = 'Up'; HardwareInterface = $true; Description = 'Real Wi-Fi' }
    [pscustomobject]@{ Name = 'Ethernet'; InterfaceAlias = 'Ethernet'; Status = 'Up'; HardwareInterface = $true; Description = 'Real Ethernet' }
}
function Get-NetIPConfiguration {
    [pscustomobject]@{ InterfaceAlias = 'Wi-Fi'; IPv4Address = @([pscustomobject]@{ IPAddress = '192.168.137.1' }); IPv4DefaultGateway = @() }
    [pscustomobject]@{ InterfaceAlias = 'Ethernet'; IPv4Address = @([pscustomobject]@{ IPAddress = '10.0.0.2' }); IPv4DefaultGateway = @() }
}
. '.\scripts\network_helpers.ps1'
$r = Resolve-ToolLightHouseLanIPv4
Write-Output "$($r.IPAddress)|$($r.Ambiguous)|$($r.Candidates -join ',')"
"""
    res = _run_ps_command(ps_cmd, cwd=tmp_path)
    assert res.returncode == 0, res.stderr
    assert "|True|" in res.stdout
    assert "192.168.137.1" in res.stdout and "10.0.0.2" in res.stdout
