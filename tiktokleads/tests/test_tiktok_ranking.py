from backend.scraper.tiktok_ranking import email_likelihood_score, sort_discovery_items_by_email_likelihood


def test_email_likelihood_score_prioritizes_contact_signals():
    weak = {"author_handle": "weak.user", "description": "contenido diario", "hashtags": ["salud"]}
    strong = {
        "author_handle": "strong.user",
        "description": "Contacto: nutrition.mail@gmail.com para collab/booking",
        "hashtags": ["contacto", "business"],
    }
    assert email_likelihood_score(strong) > email_likelihood_score(weak)


def test_sort_discovery_items_by_email_likelihood_orders_best_first():
    items = [
        {"author_handle": "c_user", "description": "tips", "hashtags": ["fit"]},
        {
            "author_handle": "a_user",
            "description": "email: hello@brand.com",
            "hashtags": ["business"],
        },
        {"author_handle": "b_user", "description": "whatsapp para contacto", "hashtags": []},
    ]
    out = sort_discovery_items_by_email_likelihood(items)
    assert out[0]["author_handle"] == "a_user"
    assert out[-1]["author_handle"] == "c_user"


def test_sort_discovery_items_by_email_likelihood_tie_breaks_by_handle():
    items = [
        {"author_handle": "zeta", "description": "sin señales", "hashtags": []},
        {"author_handle": "alpha", "description": "sin señales", "hashtags": []},
    ]
    out = sort_discovery_items_by_email_likelihood(items)
    assert [x["author_handle"] for x in out] == ["alpha", "zeta"]
