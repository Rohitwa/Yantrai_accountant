"""The films on the built pages: the redesigned English home plays the product
film (Hindi or English, a 9:16 cut on phones held upright), the French site
keeps its own silent cut. Reads public/ as built; no server."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC = os.path.join(ROOT, "public")
PORTRAIT = "(max-width: 760px) and (orientation: portrait)"


def page(*parts):
    return open(os.path.join(PUBLIC, *parts, "index.html"), encoding="utf-8").read()


def video_tag(html):
    tags = re.findall(r"<video\b[^>]*>", html)
    assert len(tags) == 1
    return tags[0]


def test_english_home_plays_the_product_film_in_two_languages_and_two_shapes():
    html = page()
    tag = video_tag(html)
    assert 'preload="none"' in tag
    assert 'poster="/assets/ohm-film-poster.jpg"' in tag
    assert 'data-poster-land="/assets/ohm-film-poster.jpg"' in tag
    assert 'data-poster-port="/assets/ohm-film-poster-9x16.jpg"' in tag
    # Hindi and English, each wide and upright: the same files the workspace invites with
    for name in ("ohm-invite.mp4", "ohm-invite-9x16.mp4", "ohm-invite-en.mp4", "ohm-invite-en-9x16.mp4"):
        assert f"'/assets/{name}'" in html, name
    assert "aifa-pipeline" not in html and "ohm-explainer" not in html   # nothing of the old cuts


def test_redesigned_pages_point_at_files_that_exist_and_carry_no_inline_images():
    for parts in ((), ("accountant",), ("auditor",), ("command",), ("pricing",), ("customers",),
                  ("security",), ("partners",), ("about",)):
        html = page(*parts)
        assert "data:image" not in html and '.html"' not in html, parts
        for ref in set(re.findall(r"""["'](/assets/[^"']+)["']""", html)):
            assert os.path.isfile(os.path.join(PUBLIC, ref.lstrip("/"))), (parts, ref)
        for href in set(re.findall(r'href="(/[^"#?]*)', html)):
            target = os.path.join(PUBLIC, href.strip("/"))
            assert href == "/" or os.path.isfile(target) or os.path.isfile(os.path.join(target, "index.html")), (parts, href)


def test_french_home_keeps_its_own_silent_cut():
    html = page("fr")
    tag = video_tag(html)
    assert 'data-video-src="/assets/aifa-pipeline-60s-fr.mp4"' in tag
    assert "portrait" not in tag
    assert "data-sound-btn" not in html
    assert 'href="/assets/aifa-pipeline-poster-fr.jpg"' in html.split("</head>")[0]


def test_the_player_uses_the_same_phone_rule_as_the_build():
    app_js = open(os.path.join(PUBLIC, "app.js"), encoding="utf-8").read()
    assert f"window.matchMedia('{PORTRAIT}')" in app_js
    assert "data-video-portrait-src" in app_js and "[data-sound-btn]" in app_js
    css = open(os.path.join(PUBLIC, "site.css"), encoding="utf-8").read()
    assert "[data-video-wrap][data-portrait] { aspect-ratio: 9 / 16 !important; }" in css


def test_the_video_files_are_web_ready():
    for name, limit in (("ohm-explainer.mp4", 8), ("ohm-explainer-9x16.mp4", 8),
                        ("ohm-invite.mp4", 8), ("ohm-invite-9x16.mp4", 8),
                        ("ohm-invite-en.mp4", 8), ("ohm-invite-en-9x16.mp4", 8)):
        data = open(os.path.join(PUBLIC, "assets", name), "rb").read()
        assert 1 << 20 < len(data) < limit << 20, name          # small enough for a homepage
        assert data.index(b"moov") < data.index(b"mdat"), name   # faststart: plays while loading
    for name in ("ohm-explainer-poster.jpg", "ohm-explainer-poster-9x16.jpg",
                 "ohm-film-poster.jpg", "ohm-film-poster-9x16.jpg"):
        data = open(os.path.join(PUBLIC, "assets", name), "rb").read()
        assert data[:3] == b"\xff\xd8\xff" and len(data) < 200 * 1024, name
