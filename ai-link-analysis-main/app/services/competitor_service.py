import asyncio
import logging
import re
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

from app.metrics import measure
from app.schemas import Competitor
from app.services.evidence import (
    citation_url,
    mentions,
    positive_mention,
    sanitize_evidence,
)
from app.services.website_extractor import WebsiteExtractor, clean
from app.utils.validators import AnalysisError, validate_url

logger = logging.getLogger(__name__)
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
USER_AGENT = (
    "DxoraLinkAnalysis/1.0 (https://github.com/ChereddyGowtham11/ai-link-analysis)"
)


@dataclass(frozen=True)
class CandidateSource:
    url: str
    title: str
    excerpt: str = ""
    reference: str | None = None


def suggestion(name: str) -> Competitor:
    return Competitor(
        name=name,
        website=None,
        main_services=[],
        target_market="",
        strengths=[],
        weaknesses=[],
        competitive_gap="",
        evidence_status="unverified",
        sources=[],
    )


def unverified_suggestions(
    candidates: list[Competitor], brand_name: str
) -> list[Competitor]:
    """Keep candidate names when independent competitor evidence is unavailable."""
    seen = {" ".join(brand_name.casefold().split())}
    suggestions = []
    for candidate in candidates:
        name = candidate.name
        key = " ".join(name.casefold().split())
        if not key or key in seen or len(name) > 100 or "://" in name:
            continue
        seen.add(key)
        suggestions.append(suggestion(name))
    return suggestions


def domain(url: httpx.URL) -> str:
    return url.host.removeprefix("www.")


async def research_competitors(
    candidates: list[Competitor],
    brand_name: str,
    subject_url: str,
    offerings: list[str],
    extractor: WebsiteExtractor,
    *,
    market: str = "",
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[list[Competitor], str]:
    competitors = unverified_suggestions(candidates, brand_name)
    secret = extractor.settings.brave_search_api_key
    key = secret.get_secret_value().strip() if secret else ""
    key = "" if key == "your_api_key_here" else key
    if not key and extractor.settings.competitor_research != "wikidata":
        return competitors, "Competitor suggestions are unverified. Search is disabled."
    terms = list(
        dict.fromkeys(clean(term) for term in offerings if 4 <= len(clean(term)) <= 80)
    )[:5]
    if not terms or (not competitors and not market):
        return (
            competitors,
            "The analysis found too little evidence to research competitors.",
        )
    source_domain = domain(httpx.URL(subject_url))
    seen = {source_domain}
    incomplete = False
    try:
        with measure("competitors"):
            async with (
                asyncio.timeout(16),
                httpx.AsyncClient(
                    timeout=4,
                    trust_env=False,
                    follow_redirects=False,
                    transport=transport,
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                ) as client,
            ):
                for index, competitor in enumerate(competitors):
                    try:
                        sources = (
                            await brave_sources(client, competitor.name, terms[0], key)
                            if key
                            else await wikidata_sources(client, competitor.name)
                        )
                    except (
                        httpx.HTTPError,
                        AttributeError,
                        TypeError,
                        ValueError,
                    ) as error:
                        incomplete = True
                        if isinstance(error, httpx.HTTPStatusError) and (
                            error.response.status_code in (401, 403, 429)
                        ):
                            break
                        continue
                    researched = await find_competitor(
                        competitor, terms, seen, extractor, sources
                    )
                    if researched:
                        competitors[index] = researched
                if (
                    not key
                    and not incomplete
                    and sum(row.evidence_status == "verified" for row in competitors)
                    < 3
                ):
                    known = {
                        brand_name.casefold(),
                        *(row.name.casefold() for row in competitors),
                    }
                    for source in await wikidata_market_sources(client, market):
                        name = source.title.strip()
                        if not name or name.casefold() in known or len(name) > 100:
                            continue
                        known.add(name.casefold())
                        researched = await find_competitor(
                            suggestion(name), terms, seen, extractor, [source]
                        )
                        if researched:
                            competitors.append(researched)
                        if (
                            sum(
                                row.evidence_status == "verified" for row in competitors
                            )
                            >= 3
                        ):
                            break
    except (TimeoutError, httpx.HTTPError, AttributeError, TypeError, ValueError):
        incomplete = True
    competitors.sort(
        key=lambda row: {"verified": 0, "inferred": 1, "unverified": 2}[
            row.evidence_status
        ]
    )
    competitors = competitors[:5]
    if incomplete:
        logger.warning("competitor_research incomplete")
        return (
            competitors,
            "Competitor research was incomplete. Check each evidence label.",
        )
    verified = sum(row.evidence_status == "verified" for row in competitors)
    return competitors, (
        f"Competitors with matching first-party evidence: {verified}. "
        "Inferred entries cite discovery sources. Coverage may be incomplete."
    )


async def brave_sources(
    client: httpx.AsyncClient,
    name: str,
    term: str,
    key: str,
) -> list[CandidateSource]:
    query = clean(f"{name} {term}")[:190]
    response = await client.get(
        BRAVE_URL,
        params={"q": query, "count": 4, "safesearch": "strict"},
        headers={"X-Subscription-Token": key},
    )
    response.raise_for_status()
    payload = response.json()
    web = payload.get("web") if isinstance(payload, dict) else None
    results = web.get("results") if isinstance(web, dict) else None
    if not isinstance(results, list):
        return []
    sources = []
    for item in results[:4]:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            continue
        title = BeautifulSoup(str(item.get("title") or "")[:500], "html.parser")
        excerpt = BeautifulSoup(str(item.get("description") or "")[:900], "html.parser")
        sources.append(
            CandidateSource(
                item["url"], clean(title.get_text()), clean(excerpt.get_text())
            )
        )
    return sources


async def wikidata_sources(
    client: httpx.AsyncClient, name: str
) -> list[CandidateSource]:
    response = await client.get(
        WIKIDATA_API,
        params={
            "action": "wbsearchentities",
            "search": name,
            "language": "en",
            "type": "item",
            "limit": 4,
            "format": "json",
        },
    )
    response.raise_for_status()
    hits = {
        item["id"]: item
        for item in response.json().get("search", [])[:4]
        if re.fullmatch(r"Q[1-9]\d*", item.get("id", ""))
        and mentions(item.get("label") or "", name)
    }
    if not hits:
        return []
    return (await entity_website_sources(client, list(hits), hits))[:4]


async def wikidata_market_sources(
    client: httpx.AsyncClient, market: str
) -> list[CandidateSource]:
    phrase = " ".join(re.findall(r"[\w-]+", market)[:6])
    if not 8 <= len(phrase) <= 80 or len(phrase.split()) < 2:
        return []
    response = await client.get(
        WIKIDATA_API,
        params={
            "action": "query",
            "generator": "search",
            "gsrlimit": 8,
            "gsrsearch": f'"{phrase}" haswbstatement:P856',
            "format": "json",
            "formatversion": 2,
        },
    )
    response.raise_for_status()
    pages = response.json().get("query", {}).get("pages", [])
    ids = [
        page["title"]
        for page in sorted(pages, key=lambda page: page.get("index", 0))[:8]
        if re.fullmatch(r"Q[1-9]\d*", page.get("title", ""))
    ]
    return [
        source
        for source in await entity_website_sources(client, ids)
        if mentions(source.excerpt, phrase)
    ][:8]


async def entity_website_sources(
    client: httpx.AsyncClient,
    ids: list[str],
    hits: dict | None = None,
) -> list[CandidateSource]:
    if not ids:
        return []
    response = await client.get(
        WIKIDATA_API,
        params={
            "action": "wbgetentities",
            "ids": "|".join(ids),
            "props": "claims" if hits else "labels|descriptions|claims",
            "languages": "en",
            "format": "json",
        },
    )
    response.raise_for_status()
    sources = []
    known = response.json().get("entities", {})
    for item_id in ids:
        entity = known.get(item_id)
        if not isinstance(entity, dict):
            continue
        label = (
            hits[item_id]["label"]
            if hits
            else entity.get("labels", {}).get("en", {}).get("value")
        )
        description = (
            hits[item_id].get("description")
            if hits
            else entity.get("descriptions", {}).get("en", {}).get("value")
        )
        if not isinstance(label, str):
            continue
        claims = entity.get("claims", {}).get("P856", [])
        for claim in sorted(claims, key=lambda item: item.get("rank") != "preferred")[
            :2
        ]:
            address = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
            if isinstance(address, str) and claim.get("rank") != "deprecated":
                sources.append(
                    CandidateSource(
                        address,
                        label,
                        excerpt=description or "",
                        reference=f"https://www.wikidata.org/wiki/{item_id}",
                    )
                )
    return sources


async def find_competitor(
    candidate: Competitor,
    terms: list[str],
    seen: set[str],
    extractor: WebsiteExtractor,
    sources: list[CandidateSource],
) -> Competitor | None:
    for item in sources:
        try:
            source = validate_url(item.url).copy_with(query=None)
        except AnalysisError:
            continue
        if (
            source.scheme != "https"
            or domain(source) in seen
            or not mentions(item.title, candidate.name)
        ):
            continue
        excerpt_terms = [term for term in terms if mentions(item.excerpt, term)]
        try:
            page = await extractor.fetch_page(source)
        except AnalysisError:
            continue
        final = httpx.URL(page.url)
        if final.scheme != "https" or domain(final) != domain(source):
            continue
        site_name = clean(page.data.get("brand_name"))
        site_title = clean(page.data.get("title"))
        domain_label = domain(final).split(".", 1)[0]
        brand_label = "".join(re.findall(r"[a-z0-9]", candidate.name.casefold()))
        if not mentions(site_name, candidate.name) and not (
            brand_label
            and domain_label.replace("-", "") == brand_label
            and (item.reference or mentions(site_title, candidate.name))
        ):
            continue
        sections = page.data.get("sections", {})
        offerings_text = sanitize_evidence(
            " ".join(
                [
                    site_title,
                    page.data.get("meta_description") or "",
                    sections.get("services", ""),
                    sections.get("products", ""),
                    *(heading["text"] for heading in page.data.get("headings", [])),
                ]
            )
        )
        body = sanitize_evidence(page.data.get("text") or "")
        supported = [
            term
            for term in terms
            if positive_mention(offerings_text, term) or positive_mention(body, term)
        ]
        excerpt_terms = [
            term
            for term in excerpt_terms
            if not mentions(f"{offerings_text} {body}", term) or term in supported
        ]
        if not supported and not excerpt_terms:
            continue
        offerings = supported or excerpt_terms
        subject_only = [
            term
            for term in terms
            if term not in offerings
            and not any(
                mentions(term, overlap) or mentions(overlap, term)
                for overlap in offerings
            )
        ]
        gap = ""
        if supported and subject_only:
            gap = (
                f"Both sites mention {supported[0]}. The subject site mentions "
                f"{subject_only[0]}, which was absent from the competitor's sampled "
                "offering text. Check other pages before drawing a conclusion."
            )
        url = citation_url(page.url)
        references = list(
            dict.fromkeys(
                [url, str(source)] + ([item.reference] if item.reference else [])
            )
        )
        seen.add(domain(final))
        return candidate.model_copy(
            update={
                "website": url,
                "main_services": offerings,
                "strengths": [
                    f"Highlights {term} in its public material." for term in offerings
                ],
                "competitive_gap": gap,
                "evidence_status": "verified" if supported else "inferred",
                "sources": references,
            }
        )
    return None
