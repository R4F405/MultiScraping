import os
from typing import Literal, Optional

from pydantic import BaseModel, field_validator

# Límite duro anti-baneo: nunca superar este número por ejecución
_MAX_CONTACTS_CAP = max(1, int(os.getenv("MAX_CONTACTS_CAP", "20")))


class SearchRequest(BaseModel):
    mode: Literal["index", "enrich"]
    account: str
    max_contacts: int = 20

    @field_validator("max_contacts")
    @classmethod
    def cap_max_contacts(cls, v: int) -> int:
        return max(1, min(v, _MAX_CONTACTS_CAP))


class JobResponse(BaseModel):
    id: int
    username: str
    started_at: str
    finished_at: Optional[str] = None
    contacts_scraped: int = 0
    contacts_new: int = 0
    contacts_updated: int = 0
    status: str = "done"


class LeadResponse(BaseModel):
    id: int
    username: str
    name: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    position: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    emails: Optional[str] = None
    phones: Optional[str] = None
    profile_link: Optional[str] = None
    premium: Optional[int] = None
    open_to_work: Optional[int] = None
    followers: Optional[str] = None
    connections: Optional[str] = None
    first_scraped_at: Optional[str] = None
    last_scraped_at: Optional[str] = None


class AccountResponse(BaseModel):
    username: str
    display_name: Optional[str] = None
    email: Optional[str] = None
    status: str
    last_run_at: Optional[str] = None
    proxy: Optional[str] = None
    session_exists: bool = False
    session_age_days: Optional[float] = None
    session_ok: Optional[bool] = None
    queue_pending: int = 0
    queue_done: int = 0
    queue_error: int = 0
    queue_total: int = 0
    contacts_total: int = 0
    daily_count: int = 0


class AccountAddRequest(BaseModel):
    username: Optional[str] = ""
    email: str
    password: str
    display_name: Optional[str] = ""
    proxy: Optional[str] = ""


class JobStatusResponse(BaseModel):
    running: bool
    mode: Optional[str] = None
    account: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    elapsed_seconds: Optional[int] = None
    phase: Optional[str] = None
    label: Optional[str] = None
    detail: Optional[str] = None
    current: Optional[int] = None
    total: Optional[int] = None
    percent: Optional[float] = None
    eta_seconds: Optional[int] = None
    new_count: Optional[int] = None
    updated_count: Optional[int] = None
    skipped_count: Optional[int] = None
    error_count: Optional[int] = None
    queue_pending: Optional[int] = None
    queue_done: Optional[int] = None
    queue_error: Optional[int] = None


class HealthResponse(BaseModel):
    status: str
    db_exists: bool
    accounts_count: int
    max_contacts_cap: int = _MAX_CONTACTS_CAP
    max_contacts_default: int = 20
