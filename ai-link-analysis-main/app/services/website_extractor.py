import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
from bs4 import BeautifulSoup, NavigableString, ParserRejectedMarkup, Tag

from app.config import Settings
from app.metrics import measure
from app.utils.validators import (
    AnalysisError,
    Resolver,
    resolve_dns,
    resolve_public_address,
    validate_url,
)

MAX_HTML_BYTES = 1_048_576
MAX_REDIRECTS = 3
REDIRECTS = {301, 302, 303, 307, 308}
PAGE_WORDS = re.compile(
    r"\b(about|services?|products?|solutions?|offerings?|expertise|features?)\b",
    re.IGNORECASE,
)
SECTION_WORDS = {
    "about": re.compile(r"\b(about|who we are|our story)\b", re.IGNORECASE),
    "services": re.compile(r"\b(services?|solutions?|what we do)\b", re.IGNORECASE),
    "products": re.compile(
        r"\b(products?|offerings?|catalog|features?)\b", re.IGNORECASE
    ),
}
LD_FIELDS = (
    "@type",
    "name",
    "legalName",
    "description",
    "category",
    "serviceType",
    "address",
    "areaServed",
    "telephone",
    "email",
    "makesOffer",
    "hasOfferCatalog",
    "offers",
    "itemOffered",
    "itemListElement",
    "department",
    "addressLocality",
    "addressRegion",
    "addressCountry",
    "postalCode",
    "streetAddress",
)
LD_TYPES = {
    "Organization",
    "Corporation",
    "LocalBusiness",
    "ProfessionalService",
    "Service",
    "Product",
    "Offer",
    "OfferCatalog",
    "Store",
    "Restaurant",
    "WebSite",
}
TEXT_BLOCKS = {
    "p",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "address",
    "div",
    "td",
    "dd",
    "br",
    "section",
    "article",
    "blockquote",
    "pre",
}


@dataclass
class Page:
    url: str
    data: dict[str, Any]
    links: list[httpx.URL]


@dataclass
class Website:
    pages: list[Page]
    limitations: list[str]


def clean(value: str | None) -> str:
    return " ".join((value or "").split())


def origin(url: httpx.URL) -> tuple[str, bytes, int | None]:
    return url.scheme, url.raw_host, url.port


def unique_text(parts: list[str]) -> list[str]:
    output = {}
    for part in parts:
        if part:
            output.setdefault(part.casefold(), part)
    return list(output.values())


def jsonld_value(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if isinstance(value, str):
        return clean(value)[:350]
    if isinstance(value, list):
        return [jsonld_value(item, depth + 1) for item in value[:6]]
    if isinstance(value, dict):
        return {
            key: jsonld_value(value[key], depth + 1)
            for key in LD_FIELDS
            if key in value
        }
    return None


def entity_types(entity: dict[str, Any]) -> set[str]:
    types = entity.get("@type", [])
    types = types if isinstance(types, list) else [types]
    return {item.rsplit("/", 1)[-1] for item in types if isinstance(item, str)}


def structured_data(soup: BeautifulSoup) -> list[dict[str, Any]]:
    def entities(value: Any, depth: int = 0):
        if depth > 4:
            return
        if isinstance(value, list):
            for item in value[:20]:
                yield from entities(item, depth + 1)
        elif isinstance(value, dict):
            if any(
                item in LD_TYPES
                or item.endswith("Business")
                or "address" in value
                or "hasOfferCatalog" in value
                for item in entity_types(value)
            ):
                yield jsonld_value(value)
            for key in ("@graph", "mainEntity", "publisher"):
                yield from entities(value.get(key), depth + 1)

    records = []
    for script in soup.select('script[type="application/ld+json"]')[:12]:
        raw = script.string or ""
        if not raw or len(raw) > 48_000:
            continue
        try:
            records.extend(entities(json.loads(raw)))
        except (ValueError, RecursionError):
            continue
    return records[:8]


def internal_links(soup: BeautifulSoup, page_url: httpx.URL) -> list[httpx.URL]:
    links = {}
    for tag in soup.select("a[href]"):
        href = str(tag["href"])
        if not PAGE_WORDS.search(
            f"{tag.get_text(' ', strip=True)} {href.split('?')[0]}"
        ):
            continue
        try:
            link = validate_url(str(page_url.join(href)))
        except (AnalysisError, httpx.InvalidURL):
            continue
        if origin(link) == origin(page_url) and link.path != page_url.path:
            link = link.copy_with(query=None)
            links.setdefault(str(link), link)
        if len(links) >= 12:
            break
    return list(links.values())


def visible_text(main: Tag, max_chars: int) -> str:
    parts = []
    current = []
    for node in main.descendants:
        if isinstance(node, Tag) and node.name in TEXT_BLOCKS and current:
            parts.append(clean(" ".join(current)))
            current.clear()
        elif type(node) is NavigableString and node.strip():
            current.append(node)
    parts.append(clean(" ".join(current)))
    return " ".join(unique_text(parts))[:max_chars]


def extract_page(html: str, url: httpx.URL, max_chars: int) -> Page:
    soup = BeautifulSoup(html, "html.parser")
    title = clean(soup.title.get_text(" ") if soup.title else None)[:500]
    meta = {}
    for tag in soup.find_all("meta"):
        key = str(tag.get("property") or tag.get("name") or "").lower()
        if key in {
            "description",
            "application-name",
            "og:site_name",
            "og:title",
            "og:description",
            "og:type",
            "og:url",
            "og:locale",
            "og:image",
        }:
            meta.setdefault(key, clean(str(tag.get("content", "")))[:500])
    canonical = soup.select_one('link[rel~="canonical"][href]')
    canonical_url = None
    if canonical is not None:
        try:
            canonical_url = str(validate_url(str(url.join(str(canonical["href"])))))
        except (AnalysisError, httpx.InvalidURL):
            pass
    linked_data = structured_data(soup)
    organization = next(
        (
            record
            for record in linked_data
            if entity_types(record) & {"Organization", "Corporation"}
            or (
                not entity_types(record) & {"Service", "Product", "Offer", "WebSite"}
                and any(
                    record.get(key)
                    for key in ("legalName", "address", "hasOfferCatalog")
                )
            )
        ),
        {},
    )
    links = internal_links(soup, url)

    for tag in soup.select(
        "script, style, noscript, template, svg, iframe, form, nav, footer, aside, "
        "[role=navigation], [hidden], [aria-hidden=true], "
        ".cookie-banner, #cookie-banner"
    ):
        tag.decompose()
    main = soup.select_one("main, [role=main], article") or soup.body or soup
    heading_tags = main.find_all(["h1", "h2", "h3"], limit=24)
    headings = [
        {"level": tag.name, "text": clean(tag.get_text(" "))[:250]}
        for tag in heading_tags
    ]
    sections = {}
    for key, pattern in SECTION_WORDS.items():
        snippets = []
        for heading in heading_tags:
            if pattern.search(heading.get_text(" ")):
                snippets.append(clean(heading.get_text(" ")))
                for sibling in heading.next_siblings:
                    if isinstance(sibling, Tag):
                        if sibling.name in ("h1", "h2", "h3"):
                            break
                        snippets.append(clean(sibling.get_text(" ")))
        sections[key] = " ".join(unique_text(snippets))[:900]
    for node in main.find_all(["head", "header"]):
        node.decompose()
    text = visible_text(main, max_chars)
    contacts = unique_text(
        [clean(tag.get_text(" "))[:350] for tag in main.select("address")]
        + [
            str(tag["href"]).split("?", 1)[0][:250]
            for tag in main.select('a[href^="mailto:"], a[href^="tel:"]')
        ]
    )[:8]
    return Page(
        str(url),
        {
            "title": title or None,
            "meta_description": meta.get("description") or None,
            "brand_name": meta.get("og:site_name")
            or meta.get("application-name")
            or organization.get("name")
            or organization.get("legalName"),
            "business_category": organization.get("category")
            or organization.get("@type")
            or meta.get("og:type"),
            "headings": headings,
            "text": text,
            "sections": sections,
            "contact": contacts,
            "canonical_url": canonical_url,
            "open_graph": {
                key: value for key, value in meta.items() if key.startswith("og:")
            },
            "json_ld": linked_data,
        },
        links,
    )


class WebsiteExtractor:
    def __init__(
        self,
        settings: Settings,
        resolver: Resolver = resolve_dns,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.resolver = resolver
        self.transport = transport

    async def _fetch(
        self, url: httpx.URL, same_origin: tuple[str, bytes, int | None] | None
    ) -> tuple[str, httpx.URL]:
        timeout = self.settings.http_timeout_seconds
        for hop in range(MAX_REDIRECTS + 1):
            if same_origin is not None and origin(url) != same_origin:
                raise AnalysisError(
                    400, "blocked_redirect", "Internal crawl left the website origin."
                )
            address = await resolve_public_address(url, self.resolver)
            host = url.raw_host.decode("ascii")
            pinned = url.copy_with(host=f"[{address}]" if ":" in address else address)
            # A fresh pool per hop prevents TLS connections from crossing virtual hosts.
            async with (
                httpx.AsyncClient(
                    timeout=httpx.Timeout(timeout, connect=min(5, timeout)),
                    follow_redirects=False,
                    trust_env=False,
                    transport=self.transport,
                ) as client,
                client.stream(
                    "GET",
                    pinned,
                    headers={
                        "Host": f"[{host}]" if ":" in host else host,
                        "Accept": "text/html, application/xhtml+xml",
                        "Accept-Encoding": "identity",
                        "User-Agent": "WebsiteLinkAnalysis/1.0",
                    },
                    extensions={"sni_hostname": host},
                ) as response,
            ):
                if response.status_code in REDIRECTS:
                    location = response.headers.get("location")
                    if hop == MAX_REDIRECTS or not location:
                        raise AnalysisError(
                            502,
                            "redirect_failure",
                            "Website redirects could not be followed.",
                        )
                    try:
                        url = validate_url(str(url.join(location)))
                    except httpx.InvalidURL:
                        raise AnalysisError(
                            502,
                            "redirect_failure",
                            "Website returned an invalid redirect.",
                        ) from None
                    continue
                if response.status_code in (403, 404):
                    raise AnalysisError(
                        response.status_code,
                        f"website_http_{response.status_code}",
                        "The website denied access."
                        if response.status_code == 403
                        else "The website page was not found.",
                    )
                if not response.is_success:
                    raise AnalysisError(
                        502,
                        "website_unavailable",
                        "Website returned an unsuccessful response.",
                    )
                media_type = (
                    response.headers.get("content-type", "")
                    .split(";")[0]
                    .strip()
                    .lower()
                )
                encoding = (
                    response.headers.get("content-encoding", "identity").strip().lower()
                )
                if (
                    media_type not in {"text/html", "application/xhtml+xml"}
                    or encoding != "identity"
                ):
                    raise AnalysisError(
                        415,
                        "unsupported_content",
                        "Website must return uncompressed HTML.",
                    )
                declared_size = response.headers.get("content-length", "")
                if (
                    declared_size.isascii()
                    and declared_size.isdigit()
                    and (len(declared_size) > 10 or int(declared_size) > MAX_HTML_BYTES)
                ):
                    raise AnalysisError(
                        413,
                        "website_too_large",
                        "Website response exceeded the size limit.",
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(body) + len(chunk) > MAX_HTML_BYTES:
                        raise AnalysisError(
                            413,
                            "website_too_large",
                            "Website response exceeded the size limit.",
                        )
                    body.extend(chunk)
                try:
                    return body.decode(
                        response.encoding or "utf-8", errors="replace"
                    ), url
                except (LookupError, UnicodeError):
                    return body.decode("utf-8", errors="replace"), url
        raise AnalysisError(
            502, "redirect_failure", "Website redirects could not be followed."
        )

    async def fetch_page(
        self, url: httpx.URL, same_origin: tuple[str, bytes, int | None] | None = None
    ) -> Page:
        try:
            with measure("fetch"):
                html, final_url = await self._fetch(url, same_origin)
        except httpx.TimeoutException:
            raise AnalysisError(
                504, "website_timeout", "Website did not respond in time."
            ) from None
        except (httpx.RequestError, httpx.InvalidURL):
            raise AnalysisError(
                502, "website_unavailable", "Website could not be reached."
            ) from None
        if not html.strip():
            raise AnalysisError(
                422, "empty_website", "Website returned an empty HTML response."
            )
        if not re.search(r"<[a-z][a-z0-9:-]*(?:\s|/?>)", html, re.IGNORECASE):
            raise AnalysisError(
                422, "invalid_html", "Website did not return recognizable HTML."
            )
        try:
            with measure("extract"):
                page = await asyncio.to_thread(
                    extract_page, html, final_url, self.settings.max_website_chars
                )
        except (ParserRejectedMarkup, RecursionError):
            raise AnalysisError(
                422, "invalid_html", "Website HTML could not be parsed."
            ) from None
        if not any(
            (
                page.data["text"],
                page.data["meta_description"],
                page.data["open_graph"].get("og:description"),
                page.data["json_ld"],
            )
        ):
            raise AnalysisError(
                422,
                "empty_website",
                "Website has no usable static content. It may require JavaScript.",
            )
        return page

    async def extract(self, url: httpx.URL) -> Website:
        deadline = (
            asyncio.get_running_loop().time() + self.settings.http_timeout_seconds
        )
        try:
            async with asyncio.timeout_at(deadline):
                first = await self.fetch_page(url)
        except TimeoutError:
            raise AnalysisError(
                504, "website_timeout", "Website did not respond in time."
            ) from None
        website = Website([first], [])
        if len(first.data["text"]) < 150:
            website.limitations.append(
                "The landing page provided limited static text. "
                "JavaScript content may be missing."
            )
        first_origin = origin(httpx.URL(first.url))
        seen = {first.url}
        for link in first.links[: self.settings.max_crawl_pages - 1]:
            try:
                async with asyncio.timeout_at(deadline):
                    page = await self.fetch_page(link, first_origin)
            except TimeoutError:
                website.limitations.append(
                    "The crawl deadline expired before all pages could be read."
                )
                break
            except AnalysisError:
                website.limitations.append(
                    "A linked page could not be included in the analysis."
                )
                continue
            if page.url not in seen:
                website.pages.append(page)
                seen.add(page.url)
        website.limitations = list(dict.fromkeys(website.limitations))
        return website
