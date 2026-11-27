from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import Settings
from app.services.website_extractor import (
    MAX_HTML_BYTES,
    WebsiteExtractor,
    extract_page,
)
from app.utils.validators import AnalysisError, validate_url

URL = validate_url("https://www.example.com/")
HTML = """
<html><head><title> Riverbend IT </title>
<meta name="description" content="Managed IT for offices">
<meta property="og:site_name" content="Riverbend IT">
<meta property="og:type" content="website">
<link rel="canonical" href="/canonical">
<script type="application/ld+json">{"@graph": [{"@type": "ProfessionalService",
   "name": "Riverbend IT", "address": {"addressLocality": "Pune"}}]}</script>
</head><body><nav><a href="/about">About</a><a href="/services">Services</a>
<a href="https://other.example.com/products">Products</a></nav>
<main><h1>Useful heading</h1><h2>Our services</h2>
<p>Managed IT support</p><p> MANAGED IT   SUPPORT </p><h3>Contact</h3>
<address>Pune</address><a href="mailto:info@example.com">Email</a>
<script>window.tracker = 1</script><style>unused-style</style>
<footer>Footer text</footer></main></body></html>
"""


def extractor(handler, resolver=None, **settings):
    return WebsiteExtractor(
        Settings(**settings),
        resolver=resolver or AsyncMock(return_value=["93.184.216.34"]),
        transport=httpx.MockTransport(handler),
    )


def html_response(text=HTML, **kwargs):
    return httpx.Response(
        200, text=text, headers={"content-type": "text/html"}, **kwargs
    )


def test_extracts_content_metadata_and_unique_text():
    page = extract_page(HTML, URL, 10_000)
    assert page.data["title"] == "Riverbend IT"
    assert page.data["meta_description"] == "Managed IT for offices"
    assert page.data["brand_name"] == "Riverbend IT"
    assert page.data["open_graph"]["og:type"] == "website"
    assert page.data["canonical_url"] == "https://www.example.com/canonical"
    assert page.data["json_ld"][0]["address"]["addressLocality"] == "Pune"
    assert page.data["contact"] == ["Pune", "mailto:info@example.com"]
    assert [h["level"] for h in page.data["headings"]] == ["h1", "h2", "h3"]
    assert "Managed IT support" in page.data["sections"]["services"]
    content = page.data["text"].casefold()
    assert content.count("managed it support") == 1
    assert all(
        item not in content for item in ("tracker", "unused-style", "footer text")
    )
    assert [link.path for link in page.links] == ["/about", "/services"]


@pytest.mark.parametrize("address", ["93.184.216.34", "2606:4700:4700::1111"])
async def test_connection_uses_validated_ip_with_original_tls_identity(address):
    resolver = AsyncMock(side_effect=[[address], ["10.0.0.1"]])

    def handler(request):
        assert request.url.raw_host.decode() == address
        assert request.headers["host"] == "www.example.com"
        assert request.extensions["sni_hostname"] == "www.example.com"
        assert request.headers["accept-encoding"] == "identity"
        return html_response()

    page = await extractor(handler, resolver).fetch_page(URL)
    assert page.url == str(URL)
    resolver.assert_awaited_once_with("www.example.com", 443)


async def test_redirect_revalidates_dns_before_connecting():
    resolver = AsyncMock(side_effect=[["93.184.216.34"], ["10.0.0.1"]])
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "/changed"})

    with pytest.raises(AnalysisError, match="must be public"):
        await extractor(handler, resolver).fetch_page(URL)
    assert len(requests) == 1
    assert resolver.await_count == 2


async def test_crawl_keeps_origin_and_page_limit():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/about":
            return httpx.Response(
                302, headers={"location": "https://another.example.com/about"}
            )
        return html_response()

    result = await extractor(handler, max_crawl_pages=3).extract(URL)
    assert paths == ["/", "/about", "/services"]
    assert [page.url for page in result.pages] == [str(URL), str(URL.join("/services"))]
    assert any("linked page" in note for note in result.limitations)


async def test_redirect_limit():
    def handler(request):
        return httpx.Response(302, headers={"location": "/redirect"})

    with pytest.raises(AnalysisError) as exc:
        await extractor(handler).fetch_page(URL)
    assert exc.value.code == "redirect_failure"


@pytest.mark.parametrize(
    "response,code,status",
    [
        (html_response(""), "empty_website", 422),
        (
            html_response("<html><div id='app'></div><script></script></html>"),
            "empty_website",
            422,
        ),
        (html_response("plain text"), "invalid_html", 422),
        (httpx.Response(200, json={}), "unsupported_content", 415),
        (httpx.Response(403), "website_http_403", 403),
        (httpx.Response(404), "website_http_404", 404),
        (httpx.Response(500), "website_unavailable", 502),
        (
            httpx.Response(
                200,
                headers={
                    "content-type": "text/html",
                    "content-length": str(MAX_HTML_BYTES + 1),
                },
            ),
            "website_too_large",
            413,
        ),
    ],
)
async def test_website_errors(response, code, status):
    with pytest.raises(AnalysisError) as exc:
        await extractor(lambda request: response).fetch_page(URL)
    assert (exc.value.code, exc.value.status) == (code, status)


async def test_unannounced_stream_size_is_bounded():
    class LargeBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(17):
                yield b"x" * 65_536

    with pytest.raises(AnalysisError) as exc:
        await extractor(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/html"}, stream=LargeBody()
            )
        ).fetch_page(URL)
    assert exc.value.code == "website_too_large"


async def test_website_timeout():
    def handler(request):
        raise httpx.ReadTimeout("upstream specific timeout", request=request)

    with pytest.raises(AnalysisError) as exc:
        await extractor(handler).extract(URL)
    assert (exc.value.code, exc.value.status) == ("website_timeout", 504)


async def test_restricted_redirect_never_reaches_transport():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/"})

    with pytest.raises(AnalysisError) as exc:
        await extractor(handler).fetch_page(URL)
    assert exc.value.code == "blocked_destination"
    assert len(calls) == 1


async def test_fetch_disables_environment_proxy_and_automatic_redirects(monkeypatch):
    create_client = httpx.AsyncClient
    recorded = []

    def record_client(**kwargs):
        recorded.append((kwargs.get("trust_env"), kwargs.get("follow_redirects")))
        return create_client(**kwargs)

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setattr(httpx, "AsyncClient", record_client)
    await extractor(lambda req: html_response()).fetch_page(URL)
    assert recorded == [(False, False)]
