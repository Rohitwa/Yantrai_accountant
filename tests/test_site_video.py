"""The explainer video on the built pages: the English site plays the OHM cut
(a 9:16 cut on phones held upright, with a sound button), the French site
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


def test_english_home_plays_the_ohm_cut_with_a_portrait_version_and_sound():
    html = page()
    tag = video_tag(html)
    assert 'data-video-src="/assets/ohm-explainer.mp4"' in tag
    assert 'data-video-portrait-src="/assets/ohm-explainer-9x16.mp4"' in tag
    assert 'data-video-portrait-poster="/assets/ohm-explainer-poster-9x16.jpg"' in tag
    assert 'poster="/assets/ohm-explainer-poster.jpg"' in tag and 'preload="none"' in tag
    buttons = re.findall(r'<button[^>]*data-sound-btn="1"[^>]*>(.*?)</button>', html, re.S)
    assert len(buttons) == 1 and "Sound off" in buttons[0]
    assert 'aria-pressed="false"' in html
    assert "aifa-pipeline" not in html                 # nothing of the old English cut


def test_each_screen_shape_preloads_only_its_own_poster():
    head = page().split("</head>")[0]
    assert (f'href="/assets/ohm-explainer-poster.jpg" media="not all and {PORTRAIT}"') in head
    assert (f'href="/assets/ohm-explainer-poster-9x16.jpg" media="{PORTRAIT}"') in head


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
    for name, limit in (("ohm-explainer.mp4", 8), ("ohm-explainer-9x16.mp4", 8)):
        data = open(os.path.join(PUBLIC, "assets", name), "rb").read()
        assert 1 << 20 < len(data) < limit << 20, name          # small enough for a homepage
        assert data.index(b"moov") < data.index(b"mdat"), name   # faststart: plays while loading
    for name in ("ohm-explainer-poster.jpg", "ohm-explainer-poster-9x16.jpg"):
        data = open(os.path.join(PUBLIC, "assets", name), "rb").read()
        assert data[:3] == b"\xff\xd8\xff" and len(data) < 200 * 1024, name
