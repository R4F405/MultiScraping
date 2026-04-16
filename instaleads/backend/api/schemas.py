from typing import Literal

from pydantic import AnyUrl, BaseModel, Field, field_validator


class SearchRequest(BaseModel):
    mode: Literal["dorking", "followers"]
    target: str = Field(..., min_length=1, description="Niche+location string or Instagram username")
    email_goal: int = Field(..., ge=1, le=500, description="Stop when this many emails are found")

    @field_validator("target")
    @classmethod
    def target_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("target cannot be blank")
        return v.strip()


class JobResponse(BaseModel):
    job_id: str
    mode: str
    target: str
    status: str
    progress: int
    total: int
    emails_found: int
    status_detail: str | None = None
    next_retry_at: str | None = None
    resume_count: int | None = 0
    profiles_scanned: int | None = 0
    enrichment_attempts: int | None = 0
    enrichment_successes: int | None = 0
    emails_from_ig: int | None = 0
    emails_from_web: int | None = 0
    skipped_private: int | None = 0
    discovery_google: int | None = 0
    discovery_duckduckgo: int | None = 0
    discovery_hashtag_api: int | None = 0
    discovery_location_api: int | None = 0
    discovery_fallback: int | None = 0
    profile_fetch_failures: int | None = 0
    enrichment_failures: int | None = 0
    failure_reason: str | None = None
    last_error: str | None = None
    started_at: str
    finished_at: str | None = None


class LeadResponse(BaseModel):
    id: int
    job_id: str
    username: str
    full_name: str | None = None
    email: str
    email_source: str | None = None
    followers_count: int | None = None
    is_business: bool = False
    bio_url: str | None = None
    profile_url: str | None = None
    source_type: str | None = None
    phone: str | None = None
    business_category: str | None = None
    scraped_at: str


class HealthResponse(BaseModel):
    status: str
    session_active: bool
    unauth_today: int
    auth_today: int
    auth_this_hour: int
    consecutive_errors: int
    last_error: str | None = None
    proxy_configured: bool = False
    discovery_strategies: dict = {}
    limits: dict


class ProfilePreview(BaseModel):
    username: str
    full_name: str | None = None
    biography: str | None = None
    bio_url: str | AnyUrl | None = None
    is_business_account: bool = False
    follower_count: int | None = None
    profile_pic_url: str | AnyUrl | None = None
    email: str | None = None
    email_source: str | None = None
    is_private: bool = False
    phone: str | None = None
    business_category: str | None = None


class AccountAddRequest(BaseModel):
    username: str
    password: str
    proxy_url: str | None = None


class AccountResponse(BaseModel):
    username: str
    status: str
    proxy_url: str | None = None
    requests_this_hour: int = 0
    has_session: bool = False


class SessionLoginRequest(BaseModel):
    username: str
    password: str


class DiagnoseResponse(BaseModel):
    blocked: bool
    rate_limited: bool
    last_error: str | None = None
    consecutive_errors: int
    session_active: bool


class ProxyStatusResponse(BaseModel):
    available: bool
    message: str
