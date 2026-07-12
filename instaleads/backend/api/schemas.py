from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    mode: str  # 'dorking' | 'followers'
    target: str  # 'niche|location' for dorking; '@account' for followers
    email_goal: int = Field(default=20, ge=1, le=500)
    # Followers mode only:
    max_results: int = Field(default=200, ge=1, le=50000)
    enrich_emails: bool = False


class DorkingRequest(BaseModel):
    niche: str
    location: str
    max_results: int = Field(default=50, ge=1, le=500)


class FollowersRequest(BaseModel):
    target: str  # target account username (with or without leading @)
    max_results: int = Field(default=200, ge=1, le=50000)
    # When True, fetch each follower's profile to extract email (slower, more
    # requests). When False (default) only the follower list is collected.
    enrich_emails: bool = False


class LimitsUpdate(BaseModel):
    daily_unauth: int | None = None


class JobResponse(BaseModel):
    job_id: str
    status: str


class LeadOut(BaseModel):
    id: int
    job_id: str | None
    instagram_id: str | None
    username: str | None
    full_name: str | None
    email: str | None
    email_source: str | None
    email_status: str | None
    phone: str | None
    website: str | None
    follower_count: int | None
    is_business: int | None
    source_type: str | None
    source_value: str | None
    scraped_at: str | None
