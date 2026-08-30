"""Bucket key construction.

Client identity keys never carry the raw peer address: a canonical form is
HMACed with a deployment-scoped salt first, so the rate-limit backend (memory
dict or Redis) never stores anything that identifies a real address.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress

ADMISSION_GLOBAL_KEY = "admission:global"


def canonicalize_peer_ip(raw: str) -> str:
    """Normalize a peer address to its canonical text form.

    Falls back to the stripped raw value for anything that does not parse as
    an IPv4 or IPv6 address (e.g. a test client's synthetic host, or a Unix
    socket peer) rather than raising: every distinct malformed value still
    gets its own bucket instead of crashing admission control.
    """

    stripped = raw.strip()
    try:
        return str(ipaddress.ip_address(stripped))
    except ValueError:
        return stripped


def hash_client_key(peer_ip: str, salt: str) -> str:
    """Derive a stable, non-reversible admission-bucket key for one client."""

    canonical = canonicalize_peer_ip(peer_ip)
    digest = hmac.new(salt.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256)
    return f"admission:client:{digest.hexdigest()}"


def provider_model_key(provider: str, model: str) -> str:
    return f"{provider}:{model}"
