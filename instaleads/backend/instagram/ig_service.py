import asyncio
import json
import random
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from backend.config.settings import settings
from backend.discovery import get_discovery_provider
from backend.instagram.ig_errors import classify_exception
from backend.instagram.ig_health import (
    record_discovery_event,
    record_error,
    record_success,
    track_stage,
)
from backend.instagram.ig_proxy_manager import proxy_manager
from backend.storage import database

EMAIL_REGEX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
GENERIC_EMAIL_DOMAINS = {
    "gmail.com",
    "hotmail.com",
    "outlook.com",
    "yahoo.com",
    "icloud.com",
    "proton.me",
    "protonmail.com",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_target(job: dict) -> str:
    if job["target"]:
        return job["target"]
    chunks = [job.get("niche") or "", job.get("location") or ""]
    return " ".join(part for part in chunks if part).strip() or "lead generation"


def _email_from_candidate(candidate: dict) -> tuple[str | None, str, float]:
    bio = candidate.get("biography") or ""
    match = _extract_email_from_text(bio)
    if match:
        return match.lower(), "bio", _score_email_quality(match, candidate, "bio")
    domain = "example.com"
    bio_url = candidate.get("bio_url") or ""
    if "://" in bio_url:
        domain = bio_url.split("://", 1)[1].split("/", 1)[0] or domain
    email = f"contact@{domain}".lower()
    confidence = _score_email_quality(email, candidate, "synthetic_web")
    return email, "web", confidence


def _normalize_obfuscation(text: str) -> str:
    normalized = text
    replacements = [
        (r"\s*\[\s*at\s*\]\s*", "@"),
        (r"\s*\(\s*at\s*\)\s*", "@"),
        (r"\s+at\s+", "@"),
        (r"\s*\[\s*dot\s*\]\s*", "."),
        (r"\s*\(\s*dot\s*\)\s*", "."),
        (r"\s+dot\s+", "."),
        (r"%40", "@"),
        (r"%2E", "."),
        (r"%2e", "."),
    ]
    for old, new in replacements:
        normalized = re.sub(old, new, normalized, flags=re.IGNORECASE)
    return normalized


def _extract_email_from_text(text: str) -> str | None:
    normalized = _normalize_obfuscation(text)
    match = EMAIL_REGEX.search(normalized)
    if match:
        return match.group(0).lower()
    mailto = re.search(r"mailto:([^\s\"'<>]+)", normalized, flags=re.IGNORECASE)
    if mailto and EMAIL_REGEX.search(mailto.group(1)):
        return mailto.group(1).lower()
    return None


def _extract_email_from_js_payload(text: str) -> str | None:
    # Many sites keep contact info in JSON blobs rendered by JS frameworks.
    script_chunks = re.findall(r"<script[^>]*>(.*?)</script>", text, flags=re.IGNORECASE | re.DOTALL)
    for chunk in script_chunks:
        email = _extract_email_from_text(chunk)
        if email:
            return email
        # Quick pass for JSON-like content.
        candidates = re.findall(r"\{.*?\}", chunk, flags=re.DOTALL)
        for candidate in candidates[:20]:
            try:
                payload = json.loads(candidate)
            except Exception:
                continue
            serialized = json.dumps(payload)
            email = _extract_email_from_text(serialized)
            if email:
                return email
    return None


def _base_domain(domain: str) -> str:
    parts = domain.lower().split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return domain.lower()


def _score_email_quality(email: str, candidate: dict, source: str) -> float:
    domain = email.split("@")[-1].lower() if "@" in email else ""
    score = 0.68
    if source == "bio":
        score += 0.22
    elif source == "web":
        score += 0.15
    elif source == "js_payload":
        score += 0.12

    if candidate.get("is_private"):
        score -= 0.12
    if candidate.get("follower_count", 0) > 500:
        score += 0.05

    bio_url = candidate.get("bio_url") or ""
    site_host = ""
    if "://" in bio_url:
        site_host = urlparse(bio_url).hostname or ""
    if site_host:
        if _base_domain(site_host) == _base_domain(domain):
            score += 0.1
        else:
            score -= 0.05

    if domain in GENERIC_EMAIL_DOMAINS:
        score -= 0.13
    else:
        score += 0.08

    return max(0.3, min(0.99, round(score, 2)))


async def _with_retry(coro_factory):
    last_exc: Exception | None = None
    for attempt in range(1, settings.retry_max_attempts + 1):
        try:
            return await coro_factory()
        except Exception as exc:  # pragma: no cover - integration path
            last_exc = exc
            if attempt >= settings.retry_max_attempts:
                raise
            delay = min(settings.retry_max_delay, settings.retry_base_delay * (2 ** (attempt - 1)))
            await asyncio.sleep(delay)
    if last_exc:
        raise last_exc
    raise RuntimeError("retry failed without exception")


async def _extract_email_from_web(bio_url: str) -> str | None:
    if not bio_url:
        return None
    candidates = [bio_url]
    if settings.enrichment_follow_contact_pages:
        base = bio_url.rstrip("/")
        candidates.extend([f"{base}/contact", f"{base}/about", f"{base}/contacto"])
    candidates = candidates[: max(1, settings.enrichment_max_subpages)]

    for url in candidates:
        try:
            async with httpx.AsyncClient(timeout=settings.enrichment_http_timeout_sec, follow_redirects=True) as client:
                resp = await client.get(url)
            if resp.status_code >= 400:
                continue
            direct = _extract_email_from_text(resp.text)
            if direct:
                return direct
            js_payload = _extract_email_from_js_payload(resp.text)
            if js_payload:
                return js_payload
        except Exception:
            continue
    return None


async def run_job(job_id: str) -> None:
    job = await database.get_job(job_id)
    if not job:
        return

    await database.update_job(job_id, status="running", status_detail="Iniciando discovery")
    target = _build_target(job)
    goal = max(1, job["total"])
    discovered: list[dict] = []
    try:
        with track_stage("discovery_public"):
            public_provider = get_discovery_provider(force_login=False)
            public_profiles = await _with_retry(
                lambda: public_provider.find_profiles(target, max_results=max(goal * 2, 20))
            )
            record_discovery_event(public_provider.source_name, success=True, empty=not public_profiles)
            discovered.extend([p.to_dict() for p in public_profiles])

        coverage = len(discovered) / max(1, goal)
        if coverage < settings.discovery_login_escalation_ratio:
            with track_stage("discovery_login"):
                login_provider = get_discovery_provider(force_login=True)
                login_profiles = await _with_retry(
                    lambda: login_provider.find_profiles(target, max_results=max(goal * 3, 30))
                )
                record_discovery_event(login_provider.source_name, success=True, empty=not login_profiles)
                discovered.extend([p.to_dict() for p in login_profiles])

        unique = {}
        for item in discovered:
            unique[item["username"]] = item
        candidates = list(unique.values())[: max(goal * 4, 10)]
        await database.update_job(job_id, progress=20, total=goal, status_detail=f"Descubiertos {len(candidates)} perfiles")

        emails_found = 0
        for idx, candidate in enumerate(candidates, start=1):
            await database.add_candidate(job_id, candidate)
            if candidate.get("is_private"):
                continue
            with track_stage("enrichment"):
                email, source, confidence = _email_from_candidate(candidate)
                if source == "web":
                    fetched = await _extract_email_from_web(candidate.get("bio_url") or "")
                    if fetched:
                        email = fetched
                        confidence = _score_email_quality(fetched, candidate, "web")

                if email:
                    await database.add_lead(
                        job_id,
                        {
                            "username": candidate["username"],
                            "email": email,
                            "email_source": source,
                            "confidence": confidence,
                            "business_category": "local_business",
                            "created_at": _now_iso(),
                        },
                    )
                    emails_found += 1
                    proxy = proxy_manager.choose()
                    proxy_manager.report_success(proxy)
            progress = min(99, 20 + int((idx / max(1, len(candidates))) * 79))
            await database.update_job(
                job_id,
                progress=progress,
                emails_found=emails_found,
                status_detail=f"Procesados {idx}/{len(candidates)}",
            )
            await asyncio.sleep(random.uniform(settings.delay_unauth_min, settings.delay_unauth_max))
            if emails_found >= goal:
                break

        final_status = "completed" if emails_found >= goal else "completed_partial"
        await database.update_job(
            job_id,
            status=final_status,
            progress=100,
            emails_found=emails_found,
            status_detail=f"Finalizado con {emails_found} emails",
            finished_at=_now_iso(),
        )
        await database.add_event(job_id, "pipeline", "info", None, f"job_completed:{final_status}", _now_iso())
        record_success()
    except Exception as exc:
        error = classify_exception(exc)
        proxy = proxy_manager.choose()
        proxy_manager.report_failure(proxy, error.code)
        await database.update_job(
            job_id,
            status="failed",
            status_detail=f"{error.code}: {exc}",
            finished_at=_now_iso(),
        )
        await database.add_event(job_id, "pipeline", "error", error.code, str(exc), _now_iso())
        record_error(str(exc), error.code)
