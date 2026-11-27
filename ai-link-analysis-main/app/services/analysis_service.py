import json
import re

import httpx

from app.metrics import measure
from app.schemas import LinkAnalysisResponse, Service
from app.services.competitor_service import research_competitors
from app.services.evidence import (
    affirmatively_offered,
    citation_url,
    location_supported,
    mentions,
    query_redactor,
    sanitize_evidence,
)
from app.services.llm_service import ModelProvider
from app.services.website_extractor import Website, WebsiteExtractor, clean

EVIDENCE_FIELDS = (
    "title",
    "meta_description",
    "brand_name",
    "business_category",
    "text",
    "headings",
    "sections",
    "canonical_url",
    "open_graph",
    "json_ld",
    "contact",
)


def evidence_payload(
    website: Website,
    budget: int,
    redactor: re.Pattern[str] | None = None,
) -> tuple[str, list[dict]]:
    def serialize() -> str:
        return json.dumps(
            {"pages": pages, "limitations": website.limitations},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def fit_text(page: dict, prefix: str, limit: int) -> None:
        low, high = 0, len(prefix)
        while low < high:
            mid = (low + high + 1) // 2
            page["text"] = prefix[:mid]
            if len(serialize()) <= limit:
                low = mid
            else:
                high = mid - 1
        page["text"] = prefix[:low]

    pages = []
    used_text = set()
    for index, page in enumerate(website.pages):
        count = len(website.pages)
        limit = (
            budget * (count + index + 1) // (2 * count) if index + 1 < count else budget
        )
        entry = {"url": citation_url(page.url, redactor)}
        pages.append(entry)
        if len(serialize()) > limit - 100 and index:
            pages.pop()
            continue
        for key in EVIDENCE_FIELDS:
            value = sanitize_evidence(page.data.get(key), redactor)
            if not value:
                continue
            if key == "text":
                if value.casefold() in used_text:
                    continue
                used_text.add(value.casefold())
                entry[key] = ""
                fit_text(entry, value, limit - min(1200, budget // 5))
                if not entry[key]:
                    entry.pop(key)
                continue
            entry[key] = value
            if len(serialize()) > limit:
                entry.pop(key)
    return serialize(), pages


def ground_services(services: list[Service], pages: list[dict]) -> list[Service]:
    evidence = {}
    for page in pages:
        headings = [item["text"] for item in page.get("headings", [])]
        evidence[page["url"]] = [
            clean(item).casefold()
            for item in [
                page.get("text", ""),
                *headings,
                *page.get("sections", {}).values(),
            ]
        ]
    for item in services:
        quote = clean(item.evidence_quote).casefold()
        name = clean(item.service_name).casefold()
        if (
            item.evidence_status == "verified"
            and name
            and any(
                affirmatively_offered(text, name, quote)
                for text in evidence.get(item.source_url, [])
            )
        ):
            continue
        item.evidence_status = "inferred"
        item.source_url = None
        item.evidence_quote = None
    seen = set()
    unique = []
    for item in sorted(services, key=lambda item: item.evidence_status != "verified"):
        name = clean(item.service_name).casefold()
        if name and name not in seen:
            unique.append(item)
            seen.add(name)
    return unique


class AnalysisService:
    def __init__(self, extractor: WebsiteExtractor, llm: ModelProvider) -> None:
        self.extractor = extractor
        self.llm = llm

    async def analyze(self, url: httpx.URL) -> LinkAnalysisResponse:
        website = await self.extractor.extract(url)
        redactor = query_redactor([str(url), *(page.url for page in website.pages)])
        payload, pages = evidence_payload(
            website,
            self.extractor.settings.max_website_chars,
            redactor,
        )
        with measure("llm"):
            result = await self.llm.analyze(payload)
        analysis = result.analysis
        competitors = analysis.competitor_analysis
        landing = website.pages[0]
        site_text = " ".join(
            str(page.get(key) or "")
            for page in pages
            for key in ("text", "headings", "sections", "meta_description", "json_ld")
        )
        offerings = []
        for item in [
            analysis.seo_improvements.primary_keyword,
            *competitors.main_products_services,
        ]:
            words = item.split()
            phrases = [item]
            if len(words) >= 3 and words[1].casefold() not in {
                "for",
                "and",
                "of",
                "to",
                "the",
            }:
                phrases.append(" ".join(words[:2]))
            offerings.extend(
                phrase for phrase in phrases if mentions(site_text, phrase)
            )
        competitors.competitors, competitor_note = await research_competitors(
            competitors.competitors,
            result.brand.name,
            landing.url,
            offerings,
            self.extractor,
            market=result.brand.category,
        )
        analysis.target_audience.geographic_focus = [
            location
            for location in analysis.target_audience.geographic_focus
            if location_supported(pages, location)
        ]
        analysis.top_services = ground_services(analysis.top_services, pages)
        verified_count = sum(
            item.evidence_status == "verified" for item in analysis.top_services
        )
        analysis.top_services_note = (
            f"Services with supporting website excerpts: {verified_count}. "
            "Inferred entries are suggestions. Fewer than five may be available."
        )
        analysis.seo_improvements.current_title = sanitize_evidence(
            landing.data["title"], redactor
        )
        analysis.seo_improvements.current_meta_description = sanitize_evidence(
            landing.data["meta_description"], redactor
        )
        limitations = [
            *website.limitations,
            "Analysis uses static website content.",
            competitor_note,
        ]
        if len(pages) < len(website.pages):
            limitations.append("The evidence limit excluded some fetched pages.")
        return LinkAnalysisResponse(
            brand=result.brand,
            analysis=analysis,
            website=citation_url(landing.url, redactor),
            pages_analyzed=[page["url"] for page in pages],
            limitations=limitations,
        )
