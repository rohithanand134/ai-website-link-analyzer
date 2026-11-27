import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

import httpx


class AnalysisError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.headers = headers


Resolver = Callable[[str, int], Awaitable[list[str]]]
DEFAULT_PORTS = {"http": 80, "https": 443}
INTERNAL_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".intranet",
    ".home",
    ".lan",
    ".corp",
    ".home.arpa",
    ".onion",
    ".invalid",
    ".test",
)
LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
BLOCKED_DESTINATION_MESSAGE = (
    "Website destination must be public. Use the site's public URL "
    "instead of localhost or a private address."
)
SPECIAL_IPS = tuple(
    map(
        ipaddress.ip_network,
        (
            "168.63.129.16/32",  # Azure host virtual IP
            "192.0.0.0/24",
            "192.88.99.0/24",
            "64:ff9b::/96",
            "64:ff9b:1::/48",
            "100::/64",
            "100:0:0:1::/64",
            "2001::/23",
            "2002::/16",
            "3fff::/20",
            "5f00::/16",
            "fec0::/10",
        ),
    )
)


def require_public_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise AnalysisError(
            502, "dns_failure", "Website address could not be resolved."
        ) from None
    if (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or "%" in value
        or (
            isinstance(address, ipaddress.IPv6Address)
            and address.ipv4_mapped is not None
        )
        or any(address in network for network in SPECIAL_IPS)
    ):
        raise AnalysisError(400, "blocked_destination", BLOCKED_DESTINATION_MESSAGE)
    return str(address)


def validate_url(value: str) -> httpx.URL:
    invalid = AnalysisError(
        400,
        "invalid_url",
        "Enter a complete public URL starting with http:// or https://, "
        "such as https://example.com.",
    )
    if not value or len(value) > 2048 or re.search(r"[\s\x00-\x1f\x7f\\]", value):
        raise invalid
    try:
        parts = urlsplit(value)
        if (
            parts.scheme not in DEFAULT_PORTS
            or not parts.hostname
            or "@" in parts.netloc
        ):
            raise invalid
        if parts.port is not None and parts.port != DEFAULT_PORTS[parts.scheme]:
            raise AnalysisError(
                400, "unsupported_port", "Use the default HTTP or HTTPS port."
            )
        url = httpx.URL(value)
        host = url.raw_host.decode("ascii").rstrip(".").lower()
        if not host or "%" in host:
            raise invalid
        try:
            ipaddress.ip_address(host)
        except ValueError:
            labels = host.split(".")
            if len(labels) < 2 or host.endswith(INTERNAL_SUFFIXES):
                raise AnalysisError(
                    400, "blocked_destination", BLOCKED_DESTINATION_MESSAGE
                )
            if len(host) > 253 or not all(LABEL.fullmatch(label) for label in labels):
                raise invalid
            if labels[-1].isdigit() or not any(char.isalpha() for char in labels[-1]):
                raise invalid
        else:
            require_public_ip(host)
        return url.copy_with(host=host, fragment=None)
    except (ValueError, UnicodeError, httpx.InvalidURL):
        raise invalid from None


async def resolve_dns(host: str, port: int) -> list[str]:
    # Absolute DNS name avoids applying system search suffixes.
    entries = await asyncio.get_running_loop().getaddrinfo(
        host + ".", port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    return list(dict.fromkeys(entry[4][0] for entry in entries))


async def resolve_public_address(
    url: httpx.URL, resolver: Resolver = resolve_dns
) -> str:
    host = url.raw_host.decode("ascii")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            answers = await resolver(host, url.port or DEFAULT_PORTS[url.scheme])
        except OSError:
            raise AnalysisError(
                502, "dns_failure", "Website address could not be resolved."
            ) from None
        if not answers:
            raise AnalysisError(
                502, "dns_failure", "Website address could not be resolved."
            )
        # Reject mixed public/private answers before choosing a connection address.
        public_addresses = [require_public_ip(answer) for answer in answers]
        return public_addresses[0]
    return require_public_ip(host)
