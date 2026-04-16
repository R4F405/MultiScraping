from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    target: str = Field(..., min_length=1, description="Hashtag o keyword (#fotografo, diseñador barcelona)")
    email_goal: int = Field(..., ge=1, le=200, description="Número objetivo de emails a encontrar")
    min_followers: int = Field(0, ge=0, description="Mínimo de seguidores para incluir un perfil")


class JobResponse(BaseModel):
    job_id: str
    target: str
    status: str
    progress: int
    total: int
    emails_found: int
    profiles_scanned: int | None = 0
    emails_from_bio: int | None = 0
    emails_from_web: int | None = 0
    skipped_count: int | None = 0
    status_detail: str | None = None
    failure_reason: str | None = None
    last_error: str | None = None
    started_at: str
    finished_at: str | None = None


class LeadResponse(BaseModel):
    id: int
    job_id: str
    username: str
    nickname: str | None = None
    email: str
    email_source: str | None = None
    followers_count: int | None = None
    verified: bool = False
    bio_link: str | None = None
    profile_url: str | None = None
    bio_text: str | None = None
    scraped_at: str


class HealthResponse(BaseModel):
    status: str
    requests_today: int
    requests_this_hour: int
    consecutive_errors: int
    last_error: str | None = None
    proxy_configured: bool
    headless_mode: bool
    limits: dict
