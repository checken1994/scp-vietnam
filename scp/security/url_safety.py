"""
URL Safety Helper — B310 fix.

Validates URL scheme + blocks internal/private IPs before calling urllib.urlopen.
Use this instead of urllib.request.urlopen() to satisfy B310 (CWE-22).  # nosec B310 — URL validated by SCP
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# Allowed URL schemes for outbound HTTP requests
ALLOWED_SCHEMES = frozenset({"http", "https"})

# Disallowed IP ranges (RFC1918 + loopback + link-local + multicast + reserved)
def _is_private_ip(host: str) -> bool:
    """Return True if host resolves to / is a private or loopback IP."""
    if not host:
        return True
    # Strip port
    if ":" in host and not host.startswith("["):
        host = host.rsplit(":", 1)[0]
    host = host.strip("[]")
    try:
        # Already an IP literal?
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
        except ValueError as e:
            logger.warning(f"Silent except: {e}")
        # Resolve hostname
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return True  # unresolvable = treat as unsafe
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return True
        return False
    except Exception:
        return True


def validate_url(url: str, *, allow_internal: bool = False) -> urllib.parse.ParseResult:
    """Validate URL scheme + (optionally) internal IP. Raises ValueError on disallowed."""
    if not isinstance(url, str) or not url:
        raise ValueError("URL must be a non-empty string")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"URL scheme '{parsed.scheme}' not in allowlist {sorted(ALLOWED_SCHEMES)}")
    if not parsed.hostname:
        raise ValueError("URL missing hostname")
    if not allow_internal and _is_private_ip(parsed.hostname):
        raise ValueError(f"URL host '{parsed.hostname}' resolves to internal/private IP — blocked")
    return parsed


def safe_urlopen(url: str | urllib.request.Request, *, timeout: float = 30, allow_internal: bool = False, **kwargs: Any):
    """Drop-in replacement for urllib.request.urlopen() that enforces B310 safety.  # nosec B310 — URL validated by SCP

    Args:
        url: URL string or Request object.
        timeout: Request timeout.
        allow_internal: Set True to allow internal/private IPs (e.g. for Ollama at 127.0.0.1).
        **kwargs: Forwarded to urllib.request.urlopen.  # nosec B310 — URL validated by SCP

    Returns:
        HTTP response object (same as urllib.request.urlopen).  # nosec B310 — URL validated by SCP
    """
    if isinstance(url, urllib.request.Request):
        url_str = url.full_url
    else:
        url_str = str(url)
    validate_url(url_str, allow_internal=allow_internal)
    if isinstance(url, urllib.request.Request):
        return urllib.request.urlopen(url, timeout=timeout, **kwargs)  # already validated above  # nosec B310  # noqa: S310
    return urllib.request.urlopen(url, timeout=timeout, **kwargs)  # already validated above  # nosec B310  # noqa: S310
