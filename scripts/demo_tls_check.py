#!/usr/bin/env python3
"""ToolLightHouse TLS Certificate and Key Pair Validator.

Reusable standard-library helper to validate:
1. Cryptographic match between X.509 certificate and private key via ssl.SSLContext.
2. Temporal validity (notBefore / notAfter), with optional injectable timestamp for testing.
3. Exact IP Address SAN matching (normalized IPv4/IPv6, strictly typed, avoiding prefix/substring false positives).
4. Localhost DNS vs Loopback IP SAN distinction for URL rendering.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import ssl
import sys
import time
from pathlib import Path


def validate_cert_pair(
    cert_path: str | Path,
    key_path: str | Path,
    lan_ip: str = "",
    now_ts: float | None = None,
) -> dict:
    """Validate certificate and private key pair, temporal validity, and exact SAN match."""
    cert_str = str(cert_path)
    key_str = str(key_path)

    res: dict = {
        "ok": False,
        "key_match": False,
        "valid_dates": False,
        "has_lan_ip": False,
        "has_localhost_dns": False,
        "has_localhost_ip": False,
        "not_before": "",
        "not_after": "",
        "sans": [],
        "ip_sans": [],
        "dns_sans": [],
        "error": "",
    }

    # 1. Decode certificate and evaluate dates
    try:
        decoded = ssl._ssl._test_decode_cert(cert_str)
    except Exception as e:
        res["error"] = f"Certificate decode failed: {e}"
        return res

    res["not_before"] = decoded.get("notBefore", "")
    res["not_after"] = decoded.get("notAfter", "")

    try:
        nb = ssl.cert_time_to_seconds(res["not_before"]) if res["not_before"] else 0
        na = ssl.cert_time_to_seconds(res["not_after"]) if res["not_after"] else 0
    except Exception as e:
        res["error"] = f"Invalid certificate date format: {e}"
        return res

    current_time = time.time() if now_ts is None else float(now_ts)
    if current_time < nb:
        res["error"] = f"Certificate not yet valid (valid from {res['not_before']})"
        res["valid_dates"] = False
    elif current_time > na:
        res["error"] = f"Certificate expired on {res['not_after']}"
        res["valid_dates"] = False
    else:
        res["valid_dates"] = True

    # 2. Extract and classify Subject Alternative Names
    raw_sans = decoded.get("subjectAltName", ())
    sans_list: list[str] = []
    ip_sans: list[str] = []
    dns_sans: list[str] = []

    for san_entry in raw_sans:
        if not isinstance(san_entry, tuple) or len(san_entry) != 2:
            continue
        san_type, san_val = san_entry
        sans_list.append(f"{san_type}:{san_val}")
        if san_type == "IP Address":
            try:
                norm_ip = str(ipaddress.ip_address(san_val))
                ip_sans.append(norm_ip)
            except ValueError:
                ip_sans.append(san_val)
        elif san_type == "DNS":
            dns_sans.append(san_val.lower())

    res["sans"] = sans_list
    res["ip_sans"] = ip_sans
    res["dns_sans"] = dns_sans
    res["has_localhost_dns"] = "localhost" in dns_sans
    # The launcher emits the IPv4 loopback URL; keep this flag aligned with it.
    res["has_localhost_ip"] = "127.0.0.1" in ip_sans

    # 3. Exact LAN IP matching against IP Address SANs (or DNS if lan_ip is a hostname)
    if lan_ip:
        try:
            target_ip = str(ipaddress.ip_address(lan_ip))
            res["has_lan_ip"] = target_ip in ip_sans
        except ValueError:
            res["has_lan_ip"] = lan_ip.lower() in dns_sans
    else:
        res["has_lan_ip"] = True

    # 4. Verify cryptographic match between cert and private key
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_str, key_str)
        res["key_match"] = True
    except Exception as e:
        res["key_match"] = False
        if not res["error"]:
            res["error"] = f"Certificate and private key pair mismatch or invalid: {e}"

    if res["valid_dates"] and res["key_match"] and res["has_lan_ip"]:
        res["ok"] = True

    return res


def main() -> None:
    parser = argparse.ArgumentParser(description="ToolLightHouse TLS Certificate and Key Validator")
    parser.add_argument("cert", help="Path to PEM certificate")
    parser.add_argument("key", help="Path to PEM private key")
    parser.add_argument("lan_ip", nargs="?", default="", help="Expected LAN IP address to verify in SAN")
    parser.add_argument("--now", type=float, default=None, help="Optional epoch timestamp override for date checks")
    args = parser.parse_args()

    result = validate_cert_pair(args.cert, args.key, lan_ip=args.lan_ip, now_ts=args.now)
    print(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
