"""Peer IP extraction for gateway admission control.

``Forwarded``/``X-Forwarded-For`` are only trusted when the deployment opts in
via ``CLOUDCAUSE_TRUST_PROXY_HEADERS``; otherwise a client could set either
header itself and land in someone else's rate-limit bucket, or evade its own.
"""

from __future__ import annotations

from fastapi import Request


def resolve_peer_ip(request: Request, *, trust_proxy_headers: bool) -> str:
    if trust_proxy_headers:
        forwarded = _first_forwarded_for(request.headers.get("forwarded"))
        if forwarded:
            return forwarded
        xff = request.headers.get("x-forwarded-for")
        if xff:
            first = xff.split(",")[0].strip()
            if first:
                return _strip_port(first)
    client = request.client
    return client.host if client is not None else "unknown"


def _first_forwarded_for(header_value: str | None) -> str | None:
    if not header_value:
        return None
    first_hop = header_value.split(",")[0]
    for token in first_hop.split(";"):
        key, _, value = token.strip().partition("=")
        if key.strip().lower() == "for":
            candidate = value.strip().strip('"')
            return _strip_port(candidate) if candidate else None
    return None


def _strip_port(value: str) -> str:
    value = value.strip()
    if value.startswith("["):
        # Bracketed IPv6 with an optional port: [::1]:8080
        end = value.find("]")
        return value[1:end] if end != -1 else value
    if value.count(":") == 1:
        # IPv4 with a port, or a bare interface name; a bare IPv6 address has
        # more than one colon and is returned unchanged.
        host, _, _port = value.partition(":")
        return host
    return value
