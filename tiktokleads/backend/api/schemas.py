from typing import Literal

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """Dedup entre jobs (automático): excluye handles con email en `tiktok_leads` y filas recientes en
    `tiktok_skipped` (ventana `TIKTOKLEADS_DEDUP_STALENESS_DAYS`, por defecto 3 días)."""

    mode: Literal["keyword", "hashtag"] = Field(
        default="keyword",
        description="Solo keyword y hashtag; el modo perfil-seed se retiró del producto.",
    )
    target: str = Field(
        min_length=2,
        max_length=200,
        description="Texto de búsqueda. Para priorizar zona: 'nicho|ciudad' (ej. nutricionista|Valencia).",
    )
    max_results: int = Field(default=30, ge=1, le=500)
    email_goal: int = Field(
        default=1,
        ge=1,
        le=200,
        description="Objetivo mínimo de correos por job; el worker sigue rondas hasta alcanzarlo o agotar límites.",
    )
    enrich_profiles: bool = True
    engine: str | None = Field(default=None, description="Solo tests: mock. Producción: omitir (live).")


class JobResponse(BaseModel):
    job_id: str
    status: str

