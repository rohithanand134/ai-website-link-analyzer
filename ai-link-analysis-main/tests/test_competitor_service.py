from unittest.mock import AsyncMock

import httpx

from app.config import Settings
from app.schemas import Competitor
from app.services.competitor_service import research_competitors
from app.services.website_extractor import WebsiteExtractor


def candidate(name: str) -> Competitor:
    return Competitor(
        name=name,
        website="https://invented.example",
        main_services=[],
        target_market="",
        strengths=[],
        weaknesses=["invented"],
        competitive_gap="",
        evidence_status="verified",
        sources=["https://invented.example"],
    )


async def test_competitor_research_classifies_sources_and_overlapping_offerings():
    names = ["Northstar", "Lakeside", "Juniper", "Cedar", "Maple"]
    addresses = AsyncMock(return_value=["93.184.216.34"])

    def site_response(request):
        host = request.headers["host"].split(".")[0]
        detail = "IT support" if host != "cedar" else "Contact our team"
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                f"<title>{host.title()}</title>"
                f"<main><h2>Our services</h2><p>{detail}</p></main>"
            ),
        )

    def search_response(request):
        assert request.url.host == "api.search.brave.com"
        assert request.headers["x-subscription-token"] == "mock-brave-key"
        name = request.url.params["q"].split(" ")[0]
        url = (
            f"https://{name.lower()}.example/"
            if name != "Maple"
            else "http://localhost/"
        )
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": name,
                            "url": url,
                            "description": "Offers <b>IT support</b>",
                        },
                    ]
                }
            },
        )

    extractor = WebsiteExtractor(
        Settings(brave_search_api_key="mock-brave-key"),
        addresses,
        httpx.MockTransport(site_response),
    )
    rows, note = await research_competitors(
        [candidate(name) for name in names],
        "Subject",
        "https://subject.example",
        ["IT support", "Onsite training"],
        extractor,
        transport=httpx.MockTransport(search_response),
    )
    assert [item.evidence_status for item in rows] == [
        "verified",
        "verified",
        "verified",
        "inferred",
        "unverified",
    ]
    assert addresses.await_count == 4
    assert "3" in note
    for row in rows[:4]:
        assert row.main_services == ["IT support"]
        assert row.sources and row.sources[0] == row.website
        assert row.strengths
        assert row.weaknesses == []
    assert rows[-1].website is None and rows[-1].sources == []
    assert "Onsite training" in rows[0].competitive_gap
    assert rows[3].competitive_gap == ""


async def test_wikidata_discovers_competitors_when_the_model_omits_them():
    names = ["Northstar", "Lakeside", "Juniper"]

    def lookup(request):
        if request.url.params["action"] == "query":
            assert (
                request.url.params["gsrsearch"]
                == '"IT support services" haswbstatement:P856'
            )
            return httpx.Response(
                200,
                json={
                    "query": {
                        "pages": [
                            {"title": f"Q{index}", "index": index}
                            for index, _ in enumerate(names, 1)
                        ]
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "entities": {
                    f"Q{index}": {
                        "labels": {"en": {"value": name}},
                        "descriptions": {
                            "en": {"value": "Provider of IT support services"}
                        },
                        "claims": {
                            "P856": [
                                {
                                    "mainsnak": {
                                        "datavalue": {
                                            "value": f"https://{name.lower()}.example/"
                                        }
                                    }
                                },
                            ]
                        },
                    }
                    for index, name in enumerate(names, 1)
                }
            },
        )

    def site(request):
        name = request.headers["host"].split(".")[0].title()
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=f"<title>{name}</title><main><p>We offer IT support.</p></main>",
        )

    extractor = WebsiteExtractor(
        Settings(brave_search_api_key=None, competitor_research="wikidata"),
        AsyncMock(return_value=["93.184.216.34"]),
        httpx.MockTransport(site),
    )
    rows, _ = await research_competitors(
        [],
        "Subject",
        "https://subject.example/",
        ["IT support"],
        extractor,
        market="IT support services",
        transport=httpx.MockTransport(lookup),
    )
    assert [row.name for row in rows] == names
    assert all(
        row.evidence_status == "verified" and len(row.sources) == 2 for row in rows
    )


async def test_optional_search_failure_preserves_unverified_suggestions():
    extractor = WebsiteExtractor(Settings(brave_search_api_key="mock-brave-key"))
    rows, note = await research_competitors(
        [candidate("Northstar")],
        "Subject",
        "https://subject.example",
        ["IT support"],
        extractor,
        transport=httpx.MockTransport(lambda req: httpx.Response(429)),
    )
    assert rows[0].evidence_status == "unverified" and not rows[0].sources
    assert "incomplete" in note


async def test_wikidata_needs_no_key_and_cites_identity_and_offering():
    name = "Northstar"
    source = "https://northstar.example/"

    def lookup(request):
        assert request.url.host == "www.wikidata.org"
        assert "x-subscription-token" not in request.headers
        if request.url.params["action"] == "wbsearchentities":
            return httpx.Response(200, json={"search": [{"id": "Q42", "label": name}]})
        assert request.url.params["ids"] == "Q42"
        return httpx.Response(
            200,
            json={
                "entities": {
                    "Q42": {
                        "claims": {
                            "P856": [{"mainsnak": {"datavalue": {"value": source}}}]
                        }
                    }
                }
            },
        )

    extractor = WebsiteExtractor(
        Settings(brave_search_api_key=None, competitor_research="wikidata"),
        AsyncMock(return_value=["93.184.216.34"]),
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<title>Northstar</title><main><h2>IT support</h2></main>",
            )
        ),
    )
    rows, _ = await research_competitors(
        [candidate(name)],
        "Subject",
        "https://subject.example",
        ["IT support", "Onsite training"],
        extractor,
        transport=httpx.MockTransport(lookup),
    )
    assert rows[0].evidence_status == "verified"
    assert rows[0].sources == [source, "https://www.wikidata.org/wiki/Q42"]
    assert "Onsite training" in rows[0].competitive_gap
