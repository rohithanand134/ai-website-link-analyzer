from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, model_validator

Platform = Literal["Google", "Facebook", "Instagram", "LinkedIn", "X"]
Inferred = Annotated[
    Literal["inferred"], WithJsonSchema({"type": "string", "enum": ["inferred"]})
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class LinkAnalysisRequest(StrictModel):
    url: str = Field(min_length=1, max_length=2048, examples=["https://example.com"])


class ErrorDetail(StrictModel):
    code: str
    message: str


class ErrorResponse(StrictModel):
    success: Literal[False] = False
    error: ErrorDetail


class Brand(StrictModel):
    name: str
    category: str
    summary: str


class Competitor(StrictModel):
    name: str
    website: str | None
    main_services: list[str]
    target_market: str
    strengths: list[str]
    weaknesses: list[str]
    competitive_gap: str
    evidence_status: Literal["verified", "inferred", "unverified"]
    sources: list[str]


class CompetitorAnalysis(StrictModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    brand_overview: str
    main_products_services: list[str]
    target_market: str
    competitors: list[Competitor] = Field(max_length=5)
    strengths: list[str]
    competitive_advantages: list[str] = Field(default_factory=list)
    opportunities: list[str]


class SearchQuery(StrictModel):
    query: str
    platforms: list[Platform] = Field(min_length=1, max_length=5)
    intent: str
    business_relevance: str
    target_audience: str

    @model_validator(mode="after")
    def check_query(self) -> Self:
        if not self.query or len(set(self.platforms)) != len(self.platforms):
            raise ValueError("Queries require text and distinct platforms")
        return self


class ContentRecommendation(StrictModel):
    category: Literal[
        "service_page",
        "industry_page",
        "faq",
        "blog",
        "local_page",
        "article",
        "internal_link",
    ]
    recommendation: str
    reason: str


class SEOImprovements(StrictModel):
    primary_keyword: str
    secondary_keywords: list[str]
    long_tail_keywords: list[str]
    business_keywords: list[str]
    current_title: str | None
    recommended_title: str
    title_reason: str
    current_meta_description: str | None
    meta_description: str
    meta_description_keywords: list[str]
    meta_description_reason: str
    supporting_content: list[ContentRecommendation]


class Service(StrictModel):
    service_name: str
    description: str
    target_industry: str
    target_customer: str
    main_benefit: str
    evidence_status: Literal["verified", "inferred"]
    source_url: str | None
    evidence_quote: str | None


class AudienceSegment(StrictModel):
    segment_name: str
    industry: str
    business_type: str
    needs: list[str]
    relevant_services: list[str]
    marketing_recommendation: str
    evidence_status: Inferred


class TargetAudience(StrictModel):
    primary_audience: list[str]
    secondary_audience: list[str]
    industries: list[str]
    business_types: list[str]
    customer_profiles: list[str]
    geographic_focus: list[str]
    customer_needs: list[str]
    customer_challenges: list[str]
    customer_interests: list[str]
    audience_segments: list[AudienceSegment]
    evidence_status: Inferred
    evidence_notes: str


class Analysis(StrictModel):
    competitor_analysis: CompetitorAnalysis
    target_search_queries: list[SearchQuery] = Field(min_length=5, max_length=5)
    seo_improvements: SEOImprovements
    top_services: list[Service] = Field(max_length=5)
    top_services_note: str
    target_audience: TargetAudience

    @model_validator(mode="after")
    def distinct_queries(self) -> Self:
        queries = {
            " ".join(item.query.casefold().split())
            for item in self.target_search_queries
        }
        if len(queries) != 5:
            raise ValueError("Search queries must be distinct")
        return self


class ModelAnalysis(StrictModel):
    brand: Brand
    analysis: Analysis


class LinkAnalysisResponse(ModelAnalysis):
    success: Literal[True] = True
    website: str
    pages_analyzed: list[str]
    limitations: list[str]
