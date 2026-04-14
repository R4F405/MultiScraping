from pydantic import BaseModel, Field, model_validator


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="What to search (e.g. 'dentistas')")
    location: str = Field(default="", description="Text location fallback (e.g. 'Valencia')")
    lat: float | None = Field(default=None, description="Latitude of the search center")
    lng: float | None = Field(default=None, description="Longitude of the search center")
    radius_km: float = Field(default=10.0, ge=1.0, le=50.0, description="Search radius in km")
    max_results: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def require_location_or_coords(self) -> "SearchRequest":
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
    category: str | None
    rating: float | None
    maps_url: str | None
    scraped_at: str | None
