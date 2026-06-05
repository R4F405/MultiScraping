"""Tests unitarios del módulo SERP/dorking (sin red)."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from backend.scraper import tiktok_serp


def test_build_dork_queries_keyword():
    qs = tiktok_serp.build_dork_queries("keyword", "café valencia")
    assert any("site:tiktok.com/@" in q for q in qs)
    assert "café valencia" in qs[0] or "café" in " ".join(qs)


def test_build_dork_queries_niche_location():
    qs = tiktok_serp.build_dork_queries("keyword", "fotógrafo|Barcelona")
    joined = " ".join(qs)
    assert "fotógrafo" in joined
    assert "Barcelona" in joined


def test_web_search_q_for_target_joins_pipe_location():
    assert tiktok_serp.web_search_q_for_target("keyword", "fotógrafo|Barcelona") == "fotógrafo Barcelona"
    assert tiktok_serp.web_search_q_for_target("hashtag", "mealprep|Valencia") == "mealprep Valencia"
    assert tiktok_serp.web_search_q_for_target("keyword", "solo texto") == "solo texto"


def test_extract_handles_from_html_with_uddg():
    html = (
        '<a href="https://www.tiktok.com/@creator_one">x</a>'
        'uddg=https%3A%2F%2Fwww.tiktok.com%2F%40other_user'
    )
    handles = tiktok_serp.extract_handles_from_html_with_uddg(html)
    assert "creator_one" in handles
    assert "other_user" in handles


def test_extract_ddg_next_params_empty():
    assert tiktok_serp.extract_ddg_next_params("") == {}


@pytest.mark.asyncio
async def test_scrape_serp_merge_one_engine_fails():
    """Si DDG falla con excepción, Startpage (mock) sigue aportando handles."""

    async def failing_post(client, url, *, headers, data):
        raise RuntimeError("blocked")

    ok = httpx.Response(200, text='<a href="https://www.tiktok.com/@from_sp">x</a>')

    async def ok_get(client, url, *, headers, params=None):
        if "startpage.com" in url:
            return ok
        return httpx.Response(404)

    with patch.object(tiktok_serp.Settings, "TIKTOKLEADS_SERP_INCLUDE_BING", False):
        async with httpx.AsyncClient() as client:
            handles = await tiktok_serp.scrape_serp_handles_for_query(
                failing_post,
                ok_get,
                client,
                "site:tiktok.com/@ test",
                {"User-Agent": "test"},
                log_event=None,
            )
    assert "from_sp" in handles


@pytest.mark.asyncio
async def test_scrape_serp_both_engines_merge_dedup():
    ddg = httpx.Response(
        200,
        text='<a href="https://www.tiktok.com/@user_a">x</a>',
    )
    sp = httpx.Response(
        200,
        text=(
            '<a href="https://www.tiktok.com/@user_a">dup</a>'
            '<a href="https://www.tiktok.com/@user_b">y</a>'
        ),
    )

    async def mock_post(client, url, *, headers, data):
        if "duckduckgo.com/html" in url:
            return ddg
        return None

    async def mock_get(client, url, *, headers, params=None):
        if "startpage.com" in url:
            return sp
        return None

    with patch.object(tiktok_serp.Settings, "TIKTOKLEADS_SERP_INCLUDE_BING", False):
        async with httpx.AsyncClient() as client:
            handles = await tiktok_serp.scrape_serp_handles_for_query(
                mock_post,
                mock_get,
                client,
                "site:tiktok.com/@ niche",
                {"User-Agent": "test"},
                log_event=AsyncMock(),
            )
    assert handles == ["user_a", "user_b"]
