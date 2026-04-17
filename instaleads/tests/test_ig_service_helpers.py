from backend.instagram.ig_service import (
    _extract_email_from_js_payload,
    _extract_email_from_text,
    _score_email_quality,
)


def test_extract_email_from_obfuscated_text():
    text = "Contacto: ventas (at) miempresa (dot) com"
    email = _extract_email_from_text(text)
    assert email == "ventas@miempresa.com"


def test_extract_email_from_js_payload():
    html = """
    <html><body>
      <script type="application/json">
        {"contact":{"email":"hola@acme-shop.com"}}
      </script>
    </body></html>
    """
    email = _extract_email_from_js_payload(html)
    assert email == "hola@acme-shop.com"


def test_quality_score_corporate_beats_generic():
    candidate = {
        "is_private": False,
        "follower_count": 2500,
        "bio_url": "https://acme-shop.com/contact",
    }
    corporate = _score_email_quality("team@acme-shop.com", candidate, "web")
    generic = _score_email_quality("acme.shop@gmail.com", candidate, "web")
    assert corporate > generic
