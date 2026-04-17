from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SearchRequest(BaseModel):
    mode: Literal["single", "multi_locality"] = Field(
        default="single",
        description="Search mode. 'single' keeps current behavior, 'multi_locality' runs category + locality batch.",
    )
    query: str = Field(default="", description="What to search (e.g. 'dentistas')")
    location: str = Field(default="", description="Text location fallback (e.g. 'Valencia')")
    lat: float | None = Field(default=None, description="Latitude of the search center")
    lng: float | None = Field(default=None, description="Longitude of the search center")
    radius_km: float = Field(default=10.0, ge=1.0, le=50.0, description="Search radius in km")
    max_results: int = Field(default=50, ge=1, le=200)
    category_query: str = Field(default="", description="Business category for multi-locality mode")
    locations: list[str] = Field(default_factory=list, description="Localities for multi-locality mode")
    emails_target_per_location: int = Field(default=10, ge=1, le=200)

    @model_validator(mode="after")
    def require_location_or_coords(self) -> "SearchRequest":
        if self.mode == "multi_locality":
            if not self.category_query.strip():
                raise ValueError("'category_query' is required in multi_locality mode")
            cleaned_locations = [loc.strip() for loc in self.locations if loc.strip()]
            if not cleaned_locations:
                raise ValueError("At least one non-empty location is required in multi_locality mode")
            self.locations = cleaned_locations
            return self

        if not self.query.strip():
            raise ValueError("'query' is required in single mode")

        has_coords = self.lat is not None and self.lng is not None
        if not self.location.strip() and not has_coords:
            raise ValueError("Either 'location' or lat/lng coordinates must be provided")
        return self


class JobResponse(BaseModel):
    job_id: str
    status: str
    progress: int
    total: int
    emails_found: int
    waiting_for_proxy: bool = False
    proxy_wait_seconds: int = 0
    mode: str = "single"
    current_location_index: int = 0
    total_locations: int = 0
    current_location_label: str | None = None
    current_location_emails_found: int = 0
    emails_target_per_location: int = 0


class JobLocationResponse(BaseModel):
    id: int
    job_id: str
    location_index: int
    location_label: str
    status: str
    emails_found: int
    leads_found: int
    started_at: str | None
    finished_at: str | None


class LeadResponse(BaseModel):
    id: int
    job_id: str | None
    place_id: str | None
    business_name: str | None
    address: str | None
    phone: str | None
    website: str | None
    email: str | None
    email_status: str | None
    email_reason: str | None = None
    category: str | None
    rating: float | None
    maps_url: str | None
    scraped_at: str | None


class EmailProbeRequest(BaseModel):
    url: str = Field(description="Website URL to probe for contact emails")
