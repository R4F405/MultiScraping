"""Account pool for rotating multiple Instagram sessions in Mode B.

Each account has its own AuthLimiter so rate limits are tracked independently.
When one account hits its hourly or daily limit, the pool automatically switches
to the next available account.

Usage:
    pool = AccountPool()
    await pool.load_all_sessions()

    client, limiter, username = await pool.get_next_client()
    # use client and limiter for one scraping iteration

    # On RateLimitExceeded for that account:
    await pool.mark_rate_limited(username, retry_after_seconds=1800)
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

from backend.instagram.ig_rate_limiter import AuthLimiter, RateLimitExceeded
from backend.storage import database

logger = logging.getLogger(__name__)


@dataclass
class AccountEntry:
    username: str
    proxy_url: str | None
    client: object | None  # instagrapi.Client
    limiter: AuthLimiter
    status: str = "active"  # "active" | "cooldown" | "disabled"
    cooldown_until: float = 0.0  # monotonic timestamp
    last_used: float = field(default_factory=time.monotonic)
    is_primary: bool = False
    needs_manual_login: bool = False
    manual_login_reason: str = ""


class AccountPool:
    """Manages a pool of Instagram accounts with independent rate limits."""

    def __init__(self) -> None:
        self._accounts: dict[str, AccountEntry] = {}
        self._lock = asyncio.Lock()

    async def load_all_sessions(self) -> None:
        """Restore all saved account sessions from disk (called at startup)."""
        from backend.instagram.ig_session import load_account_session

        accounts = await database.get_all_accounts()
        first = True
        for row in accounts:
            username = row["username"]
            proxy_url = row.get("proxy_url")
            cl, error_type, message = await load_account_session(username, proxy_url=proxy_url)

            # Errors that require manual intervention from user (2FA, challenge, phone verification)
            needs_manual = error_type in ("challenge", "phone", "2fa")
            is_active = cl is not None

            # Always register the account in the pool, even if session failed
            self._accounts[username] = AccountEntry(
                username=username,
                proxy_url=proxy_url,
                client=cl,
                limiter=AuthLimiter(account_username=username),
                status="active" if is_active else "disabled",
                is_primary=first and is_active,
                needs_manual_login=needs_manual,
                manual_login_reason=message or "",
            )
            if is_active:
                first = False
                logger.info("Pool: loaded session for %s", username)
            elif needs_manual:
                logger.warning("Pool: %s needs manual login (%s): %s", username, error_type, message)
            else:
                logger.warning(
                    "Pool: could not restore session for %s (%s) — %s",
                    username,
                    error_type,
                    message,
                )

    async def add_account(
        self, username: str, password: str, proxy_url: str | None = None,
        is_primary: bool = False,
    ) -> tuple[bool, str | None, str | None]:
        """Login a new account and add it to the pool.

        Returns (success, error_type, message).
        error_type: 'challenge' | 'phone' | '2fa' | 'credentials' | 'unknown' | None
        """
        from backend.instagram.ig_session import login_account

        cl, error_type, message = await login_account(username, password, proxy_url=proxy_url)
        if cl is None:
            return False, error_type, message

        await database.save_account(username, proxy_url=proxy_url)
        async with self._lock:
            if is_primary:
                for entry in self._accounts.values():
                    entry.is_primary = False
            # Preserve limiter if account already exists (keeps rate-limit counters)
            existing = self._accounts.get(username)
            limiter = existing.limiter if existing else AuthLimiter(account_username=username)
            self._accounts[username] = AccountEntry(
                username=username,
                proxy_url=proxy_url,
                client=cl,
                limiter=limiter,
                status="active",
                is_primary=is_primary,
                needs_manual_login=False,
                manual_login_reason="",
            )
        logger.info("Pool: account %s added successfully (primary=%s)", username, is_primary)
        return True, None, None

    async def remove_account(self, username: str) -> bool:
        """Remove account from pool and delete its session + credentials."""
        from backend.instagram.ig_session import logout_account

        await logout_account(username, delete_credentials=True)
        await database.delete_account(username)
        async with self._lock:
            removed = self._accounts.pop(username, None)
        logger.info("Pool: account %s removed", username)
        return removed is not None

    async def attempt_relogin(self, username: str) -> tuple[bool, str | None, str | None]:
        """Try to re-authenticate a pool account using its saved credentials.

        Returns (success, error_type, message).
        Clears needs_manual_login on success.
        """
        from backend.instagram.ig_session import load_account_credentials, login_account

        entry = self._accounts.get(username)
        if entry is None:
            return False, "not_found", f"Cuenta '{username}' no encontrada en el pool."

        password = load_account_credentials(username)
        if not password:
            return False, "no_credentials", "No hay credenciales guardadas. Añade la cuenta de nuevo con contraseña."

        cl, error_type, message = await login_account(
            username, password, proxy_url=entry.proxy_url
        )
        async with self._lock:
            if username in self._accounts:
                if cl is not None:
                    self._accounts[username].client = cl
                    self._accounts[username].status = "active"
                    self._accounts[username].needs_manual_login = False
                    self._accounts[username].manual_login_reason = ""
                else:
                    needs_manual = error_type in ("challenge", "phone", "2fa")
                    self._accounts[username].needs_manual_login = needs_manual
                    self._accounts[username].manual_login_reason = message or ""

        if cl is not None:
            logger.info("Pool: re-login successful for %s", username)
            return True, None, None
        logger.warning("Pool: re-login failed for %s (%s): %s", username, error_type, message)
        return False, error_type, message

    async def get_next_client(self) -> tuple[object, AuthLimiter, str]:
        """Return the least-recently-used active account that is not rate-limited.

        Raises RuntimeError if no accounts are available.
        Raises RateLimitExceeded if all accounts are in cooldown or rate-limited.
        """
        async with self._lock:
            now = time.monotonic()
            candidates = [
                entry for entry in self._accounts.values()
                if entry.client is not None
                and entry.status != "disabled"
                and (entry.status != "cooldown" or now >= entry.cooldown_until)
            ]

            # Restore cooldown-expired accounts
            for entry in candidates:
                if entry.status == "cooldown" and now >= entry.cooldown_until:
                    entry.status = "active"

            if not candidates:
                raise RuntimeError("No Instagram accounts available in pool. Add accounts first.")

            # Sort by last_used ascending (least recently used first)
            candidates.sort(key=lambda e: e.last_used)

            # Pick the one whose limiter is not exhausted
            for entry in candidates:
                try:
                    await entry.limiter.check_limits()
                    entry.last_used = now
                    return entry.client, entry.limiter, entry.username
                except RateLimitExceeded:
                    continue

            # All candidates are rate-limited
            raise RateLimitExceeded(
                "All pool accounts are rate-limited.",
                retry_after_seconds=1800,
            )

    async def mark_rate_limited(self, username: str, seconds: int = 1800) -> None:
        """Put an account on cooldown for the given number of seconds."""
        async with self._lock:
            entry = self._accounts.get(username)
            if entry:
                entry.status = "cooldown"
                entry.cooldown_until = time.monotonic() + seconds
                logger.info(
                    "Pool: account %s on cooldown for %ds", username, seconds
                )

    def get_primary_info(self) -> dict | None:
        """Return session info for the primary account, or first active account."""
        primary = next((e for e in self._accounts.values() if e.is_primary), None)
        if primary is None:
            primary = next(iter(self._accounts.values()), None)
        if primary is None:
            return None
        return {
            "logged_in": True,
            "username": primary.username,
            "session_age_hours": None,
        }

    async def remove_primary(self) -> bool:
        """Remove the primary account from the pool. Falls back to first account."""
        username = None
        async with self._lock:
            primary = next((e for e in self._accounts.values() if e.is_primary), None)
            if primary is None:
                primary = next(iter(self._accounts.values()), None)
            if primary:
                username = primary.username
        if username:
            return await self.remove_account(username)
        return False

    def get_pool_status(self) -> list[dict]:
        """Return status info for all pool accounts (used by the UI)."""
        now = time.monotonic()
        result = []
        for entry in self._accounts.values():
            status = entry.status
            if status == "cooldown" and now >= entry.cooldown_until:
                status = "active"
            result.append({
                "username": entry.username,
                "status": status,
                "proxy_url": entry.proxy_url,
                "requests_this_hour": entry.limiter.count_this_hour(),
                "has_session": entry.client is not None,
                "is_primary": entry.is_primary,
                "needs_manual_login": entry.needs_manual_login,
                "manual_login_reason": entry.manual_login_reason,
            })
        return result

    def count(self) -> int:
        return len(self._accounts)

    def is_empty(self) -> bool:
        return len(self._accounts) == 0


# Module-level singleton — initialized in main.py lifespan
account_pool = AccountPool()
