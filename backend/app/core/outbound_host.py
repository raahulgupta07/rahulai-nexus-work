"""One answer to "may this server make a request to that address?".

The threat is SSRF: a caller who can name a URL the SERVER will fetch borrows
the server's network position. On any cloud host that position includes the
instance metadata endpoint, which hands out the machine's IAM credentials to
anyone who asks — no authentication, a plain GET.

★★★THIS IS DELIBERATELY NOT A BLANKET RFC1918 BLOCK, and that is the whole
design decision. This is a self-hosted analytics product: connecting to
`10.x` Postgres, a `192.168.x` Splunk, or a Druid on the same Docker network is
the NORMAL case, not the attack. A default that blocked private ranges would
break essentially every real installation on upgrade, and the operator's fix
would be to disable the guard entirely — which is worse than a narrower guard
that stays on. So the default denies exactly the addresses that are never a
legitimate analytics target:

  * cloud metadata (AWS/Azure/GCP/Oracle/Alibaba all sit on 169.254.169.254,
    plus the IPv6 and hostname forms),
  * the whole link-local range that contains it,
  * loopback — the app's own process, where an internal-only admin route
    answers without ever crossing the network,

Installs that want the stricter posture set `DASH_BLOCK_PRIVATE_HOSTS=1`, and
`DASH_OUTBOUND_HOST_ALLOWLIST` punches named exceptions through either level.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit


class OutboundHostRefused(ValueError):
    """Raised instead of making the request. Carries a user-readable sentence."""


# Hostname forms of the metadata service. Blocking the IP alone is not enough:
# `metadata.google.internal` resolves to 169.254.169.254 only from inside GCP,
# and an install could also be pointed at a resolver that maps it elsewhere.
_METADATA_HOSTNAMES = frozenset({
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
    "instance-data.ec2.internal",
})

# fd00:ec2::254 is the IMDSv6 address; the rest fall out of the link-local and
# loopback network checks below.
_METADATA_ADDRESSES = frozenset({
    "169.254.169.254",
    "fd00:ec2::254",
})


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "on", "true", "yes")


def _allowlist() -> frozenset:
    raw = os.environ.get("DASH_OUTBOUND_HOST_ALLOWLIST", "")
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def _refuse(host: str, why: str) -> None:
    # ★The message names the host and the reason but never the resolved IP of a
    # host that was allowed — an error string is a side channel, and "which
    # internal name resolves to what" is exactly what an SSRF prober wants.
    raise OutboundHostRefused(
        f"Refusing to connect to {host!r}: {why}. If this address is genuinely "
        f"your data source, add it to DASH_OUTBOUND_HOST_ALLOWLIST."
    )


def _check_ip(host: str, ip: ipaddress._BaseAddress) -> None:
    if str(ip) in _METADATA_ADDRESSES:
        _refuse(host, "it is a cloud instance-metadata endpoint")
    if ip.is_link_local:
        _refuse(host, "it is a link-local address")
    if ip.is_loopback:
        _refuse(host, "it is this server's own loopback interface")
    if ip.is_unspecified:
        _refuse(host, "it is an unspecified address")
    if _flag("DASH_BLOCK_PRIVATE_HOSTS") and ip.is_private:
        _refuse(host, "it is a private address and DASH_BLOCK_PRIVATE_HOSTS is set")


def assert_host_allowed(host: str) -> None:
    """Refuse a hostname or address the server must not be made to contact."""
    if not host:
        return
    host = host.strip().strip("[]").lower()
    if host in _allowlist():
        return
    if host in _METADATA_HOSTNAMES:
        _refuse(host, "it is a cloud instance-metadata hostname")
    if host in ("localhost", "localhost.localdomain"):
        _refuse(host, "it is this server's own loopback interface")

    try:
        _check_ip(host, ipaddress.ip_address(host))
        return
    except OutboundHostRefused:
        raise
    except ValueError:
        pass  # not a literal address — resolve it below

    # ★A name must be resolved and EVERY answer checked. A DNS record that
    # points at 169.254.169.254 is the standard way to walk past a guard that
    # only inspects the string, and a name with several A records only has to
    # get one of them past to work.
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        # Unresolvable is not our refusal to make — let the client's own
        # connection attempt produce its normal, familiar error.
        return
    for info in infos:
        addr = info[4][0]
        try:
            _check_ip(host, ipaddress.ip_address(addr.split("%")[0]))
        except ValueError:
            continue


def assert_url_allowed(url: str) -> None:
    """Same check, taking a whole URL. Refuses non-HTTP schemes outright."""
    if not url:
        return
    parts = urlsplit(url if "://" in url else f"//{url}")
    scheme = (parts.scheme or "").lower()
    if scheme and scheme not in ("http", "https", ""):
        # `file://` and `gopher://` are SSRF classics; neither is a data source.
        _refuse(url, f"the {scheme!r} scheme is not permitted for outbound requests")
    assert_host_allowed(parts.hostname or "")
