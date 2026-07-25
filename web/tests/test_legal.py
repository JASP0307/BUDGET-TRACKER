"""Privacy and security pages.

They must render for a *logged-out* visitor — someone deciding whether to trust
the app has to read them before signing up — and in both languages.
"""


def test_privacy_is_public(client):
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "Postmark" in r.text          # subprocessors are named
    assert "Telegram" in r.text


def test_security_is_public(client):
    r = client.get("/security")
    assert r.status_code == 200
    assert "Argon2id" in r.text


def test_pages_render_in_english(client):
    client.cookies.set("lang", "en")
    assert "What we store" in client.get("/privacy").text
    assert "What this project is not" in client.get("/security").text


def test_pages_render_in_spanish(client):
    client.cookies.set("lang", "es")
    assert "Qué guardamos" in client.get("/privacy").text
    assert "Lo que este proyecto no es" in client.get("/security").text


def test_privacy_discloses_operator_access(client):
    """The uncomfortable disclosure is the load-bearing one — don't let a
    reword quietly drop it."""
    text = client.get("/privacy").text
    assert "desarrollador" in text


def test_security_page_links_the_source(client):
    """The page tells users they can read the code, so the link has to be there
    and has to point at the real repository — otherwise the claim is rhetoric."""
    from web.app.routers.legal import SOURCE_URL
    text = client.get("/security").text
    assert SOURCE_URL in text
    assert SOURCE_URL.startswith("https://github.com/")


def test_security_txt_is_served(client):
    r = client.get("/.well-known/security.txt")
    assert r.status_code == 200
    assert r.text.startswith("Contact: mailto:")
    assert "Policy:" in r.text


def test_footer_links_the_pages(client):
    """The pages are worthless if nobody can find them."""
    text = client.get("/login").text
    assert 'href="/privacy"' in text and 'href="/security"' in text
