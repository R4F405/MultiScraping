from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    mode: str  # 'dorking' | 'followers'
    target: str  # 'niche|location' for dorking; '@account' for followers
    email_goal: int = Field(default=20, ge=1, le=500)
    # Followers mode only:
    max_results: int = Field(default=200, ge=1, le=50000)
    enrich_emails: bool = False
    # Ignore the saved resume cursor for this account and start over.
    reset_cursor: bool = False


class DorkingRequest(BaseModel):
    niche: str
    location: str
    max_results: int = Field(default=50, ge=1, le=500)


class FollowersRequest(BaseModel):
    target: str  # target account username (with or without leading @)
    max_results: int = Field(default=200, ge=1, le=50000)
    # When True, fetch each follower's profile to extract email in a second
    # pass, after all usernames have been collected (slower, more requests).
    # When False (default) only the follower list is collected.
    enrich_emails: bool = False
    # Ignore the saved resume cursor for this account and start over from the
    # first follower instead of continuing where a previous job left off.
    reset_cursor: bool = False


class LimitsUpdate(BaseModel):
    daily_unauth: int | None = None


class SettingsUpdate(BaseModel):
    # Only provided fields are updated. Empty string clears the DB override
    # (falls back to the .env value). Omitted (None) leaves the field untouched.
    ig_sessionid: str | None = None
    # Optional secondary account used only for Fase 2 (email enrichment),
    # kept separate from the main account that pulls the followers list.
    ig_guest_sessionid: str | None = None
    proxy_list: str | None = None
    # Rate-limit / anti-ban knobs — see backend.config.tunables.TUNABLES for
    # the full list of accepted keys. Values are raw strings; empty clears.
    limits: dict[str, str] | None = None


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
