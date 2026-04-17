from typing import Literal

from pydantic import AnyUrl, BaseModel, Field, field_validator, model_validator


class SearchRequest(BaseModel):
    mode: Literal["dorking", "followers"]
    target: str | None = Field(
        default=None,
        description="Niche+location string (mode dorking) or Instagram username (mode followers)",
    )
    niche: str | None = Field(default=None, description="Business niche for batch discovery mode")
    location: str | None = Field(default=None, description="Market location for batch discovery mode")
    language: str = Field(default="es", description="Language hint for discovery queries")
    market: str = Field(default="es", description="Market hint for discovery queries")
    email_goal: int = Field(..., ge=1, le=500, description="Stop when this many emails are found")

    @field_validator("target")
    @classmethod
    def normalize_target(cls, v: str | None) -> str | None:
        if v is None:
            return None
        value = v.strip()
        return value or None

    @field_validator("niche", "location")
    @classmethod
    def normalize_optional_text(cls, v: str | None) -> str | None:
        if v is None:
            return None
        value = v.strip()
        return value or None

    @field_validator("language", "market")
    @classmethod
    def normalize_language_market(cls, v: str) -> str:
        value = (v or "").strip().lower()
        return value or "es"

    @field_validator("email_goal")
    @classmethod
    def validate_goal(cls, v: int) -> int:
        if v < 1:
            raise ValueError("email_goal must be >= 1")
        return v

    @model_validator(mode="after")
    def validate_target_or_context(self) -> "SearchRequest":
        if self.target:
            return self
        if self.niche and self.location:
            return self
        raise ValueError("Provide target or niche+location")


class JobResponse(BaseModel):
    job_id: str
    mode: str
    target: str
    status: str
    progress: int
    total: int
    emails_found: int
    status_detail: str | None = None
    started_at: str
    finished_at: str | None = None


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
    metrics: dict = {}
    leads_today: int = 0


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
