import pytest

from backend.discovery.factory import get_discovery_provider
from backend.discovery.providers.login_provider import LoginProvider
from backend.discovery.providers.public_provider import PublicDiscoveryProvider


@pytest.mark.anyio
async def test_public_provider_returns_profiles():
    provider = PublicDiscoveryProvider()
    profiles = await provider.find_profiles("dentistas sevilla", max_results=8)
    assert len(profiles) >= 3
    assert all(p.username for p in profiles)
    assert all(p.source == "public" for p in profiles)


@pytest.mark.anyio
async def test_login_provider_returns_profiles():
    provider = LoginProvider()
    profiles = await provider.find_profiles("restaurantes bilbao", max_results=12)
    assert len(profiles) >= 5
    assert any(p.is_private for p in profiles)
    assert all(p.source == "login_pool" for p in profiles)


def test_factory_force_login():
    provider = get_discovery_provider(force_login=True)
    assert provider.source_name == "login_pool"
