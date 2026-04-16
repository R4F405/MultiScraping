from backend.scraper.category_catalog import search_categories


def test_search_categories_prefix_match():
    results = search_categories("den", limit=10)
    assert results
    assert any(r["type"] in ("dentist", "dental_clinic") for r in results)


def test_search_categories_alias_match():
    results = search_categories("asesoria", limit=10)
    assert results
    assert any(r["type"] == "accounting" for r in results)


def test_search_categories_limit_respected():
    results = search_categories("a", limit=5)
    assert len(results) <= 5


def test_search_categories_empty_query_returns_defaults():
    results = search_categories("", limit=8)
    assert len(results) <= 8
    assert len(results) > 0


def test_search_categories_supports_nutricionista():
    results = search_categories("nutricionista", limit=10)
    assert results
    assert any(r["type"] == "nutritionist" for r in results)
