import socket
from unittest.mock import AsyncMock

import pytest

from app.utils.validators import AnalysisError, resolve_public_address, validate_url


@pytest.mark.parametrize(
    "raw,host",
    [
        ("https://www.example.com/path#section", "www.example.com"),
        ("http://93.184.216.34:80/", "93.184.216.34"),
        ("https://BÜCHER.example./", "xn--bcher-kva.example"),
        ("https://[2606:4700:4700::1111]/", "2606:4700:4700::1111"),
    ],
)
def test_valid_url(raw, host):
    url = validate_url(raw)
    assert url.raw_host.decode() == host
    assert not url.fragment


@pytest.mark.parametrize(
    "raw,code",
    [
        ("example.com", "invalid_url"),
        ("ftp://example.com", "invalid_url"),
        ("https://user:secret@example.com", "invalid_url"),
        ("https://example.com:8080", "unsupported_port"),
        ("http://localhost", "blocked_destination"),
        ("http://demo.internal", "blocked_destination"),
        ("http://127.0.0.1", "blocked_destination"),
        ("http://10.0.0.1", "blocked_destination"),
        ("http://100.100.100.200", "blocked_destination"),
        ("http://169.254.169.254", "blocked_destination"),
        ("http://168.63.129.16", "blocked_destination"),
        ("http://224.0.0.1", "blocked_destination"),
        ("http://192.0.2.1", "blocked_destination"),
        ("http://[::1]", "blocked_destination"),
        ("http://[fec0::1234]", "blocked_destination"),
        ("http://[::ffff:127.0.0.1]", "blocked_destination"),
        ("https://0177.0.0.1", "invalid_url"),
        ("https://example.com\\a", "invalid_url"),
    ],
)
def test_rejected_urls(raw, code):
    with pytest.raises(AnalysisError) as exc:
        validate_url(raw)
    assert exc.value.code == code


async def test_mixed_dns_answers_rejected():
    resolver = AsyncMock(return_value=["93.184.216.34", "10.0.0.1"])
    with pytest.raises(AnalysisError, match="must be public"):
        await resolve_public_address(validate_url("https://www.example.com"), resolver)


@pytest.mark.parametrize("outcome", [[], socket.gaierror("internal resolver message")])
async def test_dns_failure(outcome):
    resolver = (
        AsyncMock(side_effect=outcome)
        if isinstance(outcome, Exception)
        else AsyncMock(return_value=outcome)
    )
    with pytest.raises(AnalysisError) as exc:
        await resolve_public_address(validate_url("https://www.example.com"), resolver)
    assert exc.value.code == "dns_failure"
    assert "internal resolver message" not in str(exc.value)
