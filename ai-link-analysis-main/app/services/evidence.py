import re
from typing import Any

import httpx

from app.services.website_extractor import clean

WEB_URL = re.compile(r"""https?://[^\s<>"']+""", re.IGNORECASE)
CLAUSE_BREAK = re.compile(
    r"(?<=[.!?;])\s+|\s+(?:but|however|whereas)\s+", re.IGNORECASE
)
OFFER = re.compile(
    r"\b(?:offer(?:s)?|provide(?:s)?|deliver(?:s)?|speciali[sz]e(?:s)? in|"
    r"include(?:s)?|features?|our services are)\b",
    re.IGNORECASE,
)
OWN_OFFERING = re.compile(
    r"\b(?:we|our|this (?:service|product|platform|software)|"
    r"the (?:platform|software|product))\b",
    re.IGNORECASE,
)
DENIAL = re.compile(
    r"\b(?:no|not|never|without|unavailable|discontinued|formerly|"
    r"previously|third[- ]party|competitor(?:s)?|planned)\b|"
    r"\b\w+n['’]t\b|\bno longer\b",
    re.IGNORECASE,
)
PLACE_CUE = re.compile(
    r"\b(?:based in|located in|headquartered in|operat(?:e|es|ing) in|"
    r"serv(?:e|es|ing)|available in|offices? in|ships? to|across)\s+(?:the\s+)?$",
    re.IGNORECASE,
)
LOCATION_FIELDS = {"addressLocality", "addressRegion", "addressCountry", "areaServed"}
SENSITIVE_QUERY = re.compile(
    r"auth|credential|key|pass|secret|session|sig|token", re.IGNORECASE
)


def citation_url(url: str, redactor: re.Pattern[str] | None = None) -> str:
    parsed = httpx.URL(url)
    safe = parsed.copy_with(query=None, fragment=None)
    if redactor and redactor.search(parsed.path):
        safe = safe.copy_with(path="/")
    return str(safe)


def query_redactor(urls: list[str]) -> re.Pattern[str] | None:
    values = {
        value
        for url in urls
        for key, value in httpx.URL(url).params.multi_items()
        if len(value) >= 16 or (len(value) >= 6 and SENSITIVE_QUERY.search(key))
    }
    if not values:
        return None
    return re.compile(
        "|".join(re.escape(value) for value in sorted(values, key=len, reverse=True))
    )


def sanitize_evidence(value: Any, redactor: re.Pattern[str] | None = None) -> Any:
    if isinstance(value, dict):
        return {key: sanitize_evidence(item, redactor) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_evidence(item, redactor) for item in value]
    if isinstance(value, str):
        text = WEB_URL.sub(
            lambda match: re.split(r"[?#]", match[0], maxsplit=1)[0], value
        )
        return redactor.sub("[redacted]", text) if redactor else text
    return value


def word_pattern(phrase: str, *, case_sensitive: bool = False) -> re.Pattern:
    return re.compile(
        rf"(?<!\w){re.escape(phrase)}(?!\w)",
        0 if case_sensitive else re.IGNORECASE,
    )


def mentions(text: str, phrase: str) -> bool:
    return bool(phrase and word_pattern(phrase).search(text))


def affirmatively_offered(text: str, name: str, quote: str) -> bool:
    if not mentions(quote, name):
        return False
    for clause in CLAUSE_BREAK.split(clean(text)):
        if not mentions(clause, quote) or DENIAL.search(clause) or clause.endswith("?"):
            continue
        offer = OFFER.search(clause)
        if (
            offer
            and OWN_OFFERING.search(clause[: offer.end()])
            and (mentions(clause[offer.end() :], name))
        ):
            return True
    return False


def positive_mention(text: str, phrase: str) -> bool:
    return any(
        mentions(clause, phrase)
        and not DENIAL.search(clause)
        and not clause.endswith("?")
        for clause in CLAUSE_BREAK.split(clean(text))
    )


def structured_locations(value: Any) -> list[str]:
    if isinstance(value, list):
        return [location for item in value for location in structured_locations(item)]
    if isinstance(value, dict):
        places = []
        for key, child in value.items():
            if key in LOCATION_FIELDS:
                if isinstance(child, str):
                    places.append(child)
                elif isinstance(child, dict) and isinstance(child.get("name"), str):
                    places.append(child["name"])
            places.extend(structured_locations(child))
        return places
    return []


def location_supported(pages: list[dict], location: str) -> bool:
    if not location:
        return False
    pattern = word_pattern(location, case_sensitive=len(location) <= 3)
    for page in pages:
        if any(
            pattern.search(place) for place in structured_locations(page.get("json_ld"))
        ):
            return True
        for text in (page.get("text") or "", page.get("meta_description") or ""):
            for match in pattern.finditer(text):
                if PLACE_CUE.search(text[max(0, match.start() - 48) : match.start()]):
                    return True
    return False
