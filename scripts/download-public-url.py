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
import urllib.error
import urllib.request
from urllib.parse import urlparse, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_empire import __version__
from claude_empire.url_safety import url_credential_issue


MAX_BYTES = 25 * 1024 * 1024
BLOCKED_HOSTS = {
    "localhost",
    "metadata",
    "metadata.google.internal",
    "metadata.azure.com",
    "metadata.ec2.internal",
}
_DARWIN_SYSTEM_SYMLINKS = frozenset(
    {"/var", "/tmp", "/etc", "/private/var", "/private/tmp", "/private/etc"}
)
_CFA_FOLDER_NAMES = frozenset(
    {"desktop", "documents", "pictures", "videos", "music", "favorites"}
)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code, "Redirects are disabled", headers, fp
        )


def _build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        NoRedirect(),
    )


def _is_symlink_or_junction(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
    except OSError:
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        try:
            if is_junction():
                return True
        except OSError:
            return True
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return bool(getattr(st, "st_file_attributes", 0) & 0x400)


def _assert_safe_output_path(output: Path) -> None:
    expanded = output.expanduser()
    if not expanded.is_absolute():
        expanded = Path(os.path.abspath(expanded))
    for curr in (expanded, *expanded.parents):
        if sys.platform == "darwin" and curr.as_posix() in _DARWIN_SYSTEM_SYMLINKS:
            continue
        if _is_symlink_or_junction(curr):
            raise ValueError(f"refused symlink or reparse point in output path: {curr}")
    if os.name == "nt":
        resolved_parts = [p.lower() for p in expanded.parts]
        for env_key in (
            "USERPROFILE",
            "OneDrive",
            "OneDriveConsumer",
            "OneDriveCommercial",
        ):
            env_val = os.environ.get(env_key, "").strip()
            if not env_val:
                continue
            base_parts = [p.lower() for p in Path(env_val).parts]
            if (
                len(resolved_parts) > len(base_parts)
                and resolved_parts[: len(base_parts)] == base_parts
                and resolved_parts[len(base_parts)] in _CFA_FOLDER_NAMES
            ):
                raise ValueError(
                    f"refused Windows Controlled Folder Access output path: {output}"
                )


def safe_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        if not safe_ip(str(ip.ipv4_mapped)):
            return False
    return bool(
        ip.is_global
        and not (
            ip.is_multicast
            or ip.is_link_local
            or ip.is_loopback
            or ip.is_private
            or ip.is_reserved
            or ip.is_unspecified
        )
    )


def resolve_public(hostname: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(
        hostname, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
    )
    ips = sorted({info[4][0].split("%", 1)[0] for info in infos})
    if not ips:
        raise ValueError(f"no DNS records for {hostname}")
    bad = [ip for ip in ips if not safe_ip(ip)]
    if bad:
        raise ValueError(f"refused non-public DNS result for {hostname}: {bad[0]}")
    return ips


def download(url: str, output: Path, max_bytes: int = MAX_BYTES) -> None:
    cred_issue = url_credential_issue(urlsplit(url))
    if cred_issue is not None:
        raise ValueError(cred_issue.message)
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("only https URLs are supported")
    if not parsed.hostname:
        raise ValueError("missing URL hostname")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in BLOCKED_HOSTS:
        raise ValueError(f"blocked hostname: {hostname}")
    port = parsed.port or 443
    _assert_safe_output_path(output)
    pinned_ip = resolve_public(hostname, port)[0]
    pinned_family = socket.AF_INET6 if ":" in pinned_ip else socket.AF_INET
    pinned_sockaddr = (
        (pinned_ip, port, 0, 0) if pinned_family == socket.AF_INET6 else (pinned_ip, port)
    )

    original_getaddrinfo = socket.getaddrinfo

    def pinned_getaddrinfo(host, requested_port, *args, **kwargs):
        if host and host.lower().rstrip(".") == hostname:
            target_port = requested_port or port
            addr = (
                (pinned_ip, target_port, 0, 0)
                if pinned_family == socket.AF_INET6
                else (pinned_ip, target_port)
            )
            return [(pinned_family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", addr)]
        return original_getaddrinfo(host, requested_port, *args, **kwargs)

    opener = _build_opener()
    req = urllib.request.Request(
        url, headers={"User-Agent": f"legends-empire/{__version__}"}
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    _assert_safe_output_path(output)
    tmp = output.with_name(f".{output.name}.tmp")
    if _is_symlink_or_junction(tmp):
        raise ValueError(f"refused symlink or reparse point temp path: {tmp}")
    try:
        socket.getaddrinfo = pinned_getaddrinfo  # type: ignore[assignment]
        with opener.open(req, timeout=20) as response:
            data = response.read(max_bytes + 1)
    finally:
        socket.getaddrinfo = original_getaddrinfo  # type: ignore[assignment]
    if len(data) > max_bytes:
        raise ValueError(f"response exceeds {max_bytes} bytes")
    if _is_symlink_or_junction(tmp) or _is_symlink_or_junction(output):
        raise ValueError("refused symlink or reparse point output path")
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
