from backend.config.settings import settings
from backend.discovery.base import DiscoveryProvider
from backend.discovery.providers.login_provider import LoginProvider
from backend.discovery.providers.public_provider import PublicDiscoveryProvider


def get_discovery_provider(force_login: bool = False) -> DiscoveryProvider:
    # Provider-free policy: only local providers are allowed.
    if force_login:
        return LoginProvider()
    provider = settings.discovery_provider.strip().lower()
    if provider in {"login", "auth", "local_login"}:
        return LoginProvider()
    return PublicDiscoveryProvider()
