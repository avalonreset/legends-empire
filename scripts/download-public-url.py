#!/usr/bin/env python3
"""Download a public HTTPS URL with SSRF guardrails for canvas attachments."""

from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path
import socket
import sys

sys.dont_write_bytecode = True
import urllib.request
from urllib.parse import urlparse


MAX_BYTES = 25 * 1024 * 1024
BLOCKED_HOSTS = {
    "localhost",
    "metadata",
    "metadata.google.internal",
    "metadata.azure.com",
    "metadata.ec2.internal",
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def safe_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return ip.is_global and not ip.is_multicast


def resolve_public(hostname: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(hostname, port, family=socket.AF_INET, type=socket.SOCK_STREAM)
    ips = sorted({info[4][0] for info in infos})
    if not ips:
        raise ValueError(f"no DNS records for {hostname}")
    bad = [ip for ip in ips if not safe_ip(ip)]
    if bad:
        raise ValueError(f"refused non-public DNS result for {hostname}: {bad[0]}")
    return ips


def download(url: str, output: Path, max_bytes: int = MAX_BYTES) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("only https URLs are supported")
    if not parsed.hostname:
        raise ValueError("missing URL hostname")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in BLOCKED_HOSTS:
        raise ValueError(f"blocked hostname: {hostname}")
    port = parsed.port or 443
    pinned_ip = resolve_public(hostname, port)[0]

    original_getaddrinfo = socket.getaddrinfo

    def pinned_getaddrinfo(host, requested_port, *args, **kwargs):
        if host and host.lower().rstrip(".") == hostname:
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (pinned_ip, requested_port or port))]
        return original_getaddrinfo(host, requested_port, *args, **kwargs)

    opener = urllib.request.build_opener(NoRedirect())
    req = urllib.request.Request(url, headers={"User-Agent": "CodexObsidian/1.9.1"})
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(f".{output.name}.tmp")
    try:
        socket.getaddrinfo = pinned_getaddrinfo  # type: ignore[assignment]
        with opener.open(req, timeout=20) as response:
            data = response.read(max_bytes + 1)
    finally:
        socket.getaddrinfo = original_getaddrinfo  # type: ignore[assignment]
    if len(data) > max_bytes:
        raise ValueError(f"response exceeds {max_bytes} bytes")
    tmp.write_bytes(data)
    os.replace(tmp, output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("output")
    args = parser.parse_args()
    try:
        download(args.url, Path(args.output))
    except Exception as exc:  # noqa: BLE001
        print(f"download-public-url: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
