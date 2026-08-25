#!/usr/bin/env python3
"""Build the static AiFA site from the pre-rendered design-canvas snapshot.

Pipeline:
  design-source/AiFA Hero White.dc.html   (canvas component, source of truth)
    -> _tpl_hooked.html                   (same component + stable data-* hooks)
    -> _captures/w1440.html               (DOM snapshot from the DC runtime)
    -> index.html                         (this script: static, no framework)

Re-run after any change to src/head.html, src/site.css or src/app.js.
Re-capture (see README) only when the canvas design itself changes.
"""
import os, re, json, html, shutil, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE_URL = os.environ.get('AIFA_SITE_URL', 'https://yantrailabs.com')
# When the site is served from '/' but its files live under a sub-path (the
# App Engine layout puts them in /aifa/), local refs need that prefix.
BASE = os.environ.get('AIFA_BASE', '').strip('/')
BASE = ('/' + BASE + '/') if BASE else ''
# Where the savings-check form posts. Same-origin on App Engine.
FORM_ENDPOINT = os.environ.get('AIFA_FORM_ENDPOINT', '')

def read(*p):
    with open(os.path.join(ROOT, *p), encoding='utf-8') as f:
        return f.read()

body = read('_captures', 'w1440.html')
helmet_css = read('_captures', 'helmet.css')
hover_css = read('_captures', 'hover.css')
head = read('src', 'head.html').replace('__SITE_URL__', SITE_URL.rstrip('/'))
form = read('src', 'form.html')
site_css = read('src', 'site.css')
app_js = read('src', 'app.js').replace("var FORM_ENDPOINT = '';",
                                       "var FORM_ENDPOINT = '%s';" % FORM_ENDPOINT)

# ---------------------------------------------------------------- hooks ---

def add_attr(tpl_id, attrs):
    """Add attributes to the element carrying data-dc-tpl="<tpl_id>"."""
    global body
    needle = '<div data-dc-tpl="%s"' % tpl_id
    assert body.count(needle) == 1, (tpl_id, body.count(needle))
    body = body.replace(needle, needle + ' ' + attrs, 1)

add_attr(6,   'data-nav="1"')
add_attr(18,  'data-nav-cta="1"')
add_attr(31,  'data-video-section="1"')
add_attr(73,  'data-section="problem"')
add_attr(191, 'data-compare="1"')
add_attr(220, 'data-section="integration"')
add_attr(752, 'data-section="demo"')
add_attr(772, 'data-footer="1"')

# The dial's inner white disc and its centre label block have no hook of their
# own; find them structurally (they are stable positions inside [data-ring]).
def hook_before(marker, attr):
    """Insert `attr` on the nearest opening <div ...> preceding `marker`."""
    global body
    idx = body.find(marker)
    assert idx > 0, marker
    start = body.rfind('<div ', 0, idx)
    body = body[:start + 5] + attr + ' ' + body[start + 5:]

hook_before('data-task-kind', 'data-ring-center="1"')
# inner disc: the sibling right after [data-ring-arc]
m = re.search(r'data-ring-arc="1"[^>]*></div>\s*<div ', body)
assert m, 'ring hole'
body = body[:m.end()] + 'data-ring-hole="1" ' + body[m.end():]

# --------------------------------------------------------- nav disclosure --
nav_toggle = (
    '<button type="button" data-nav-toggle="1" aria-expanded="false" '
    'aria-controls="nav-links" aria-label="Menu"><span></span></button>'
)
marker = '<div data-dc-tpl="18" data-nav-cta="1"'
assert body.count(marker) == 1
body = body.replace(marker, nav_toggle + marker, 1)
body = body.replace('<div data-dc-tpl="12" data-nav-links="1"',
                    '<div id="nav-links" data-dc-tpl="12" data-nav-links="1"', 1)

# `content-visibility: auto` on the two scroll-pinned sections makes Chrome
# skip painting them when the viewport jumps straight into the middle of the
# track (deep anchor link, restored scroll position, back/forward). Both are
# pinned 100vh panes driven by scroll maths, so the render-skip buys nothing.
for _sec in ('agents', 'proof'):
    _i = body.find('id="%s"' % _sec)
    assert _i > 0, _sec
    _start = body.rfind('<div ', 0, _i)
    _end = body.index('>', _i) + 1
    _tag = body[_start:_end]
    assert 'content-visibility: auto; ' in _tag, _sec
    body = body[:_start] + _tag.replace('content-visibility: auto; ', '') + body[_end:]
assert body.count('content-visibility') == 4   # problem/integration/demo/footer keep it

# ------------------------------------------------------- savings-check form
# The artboard's "Book a demo" button already points at #book, but the canvas
# has no such section. Insert it between #demo and the footer.
_i = body.find('id="footer"')
assert _i > 0
_start = body.rfind('<div ', 0, _i)
body = body[:_start] + form.strip() + '\n\n  ' + body[_start:]

# The hero CTA and the nav "Book a demo" both point at #demo, which is the
# pitch; the form directly under it is where they actually want to land.
body = body.replace('href="#demo"', 'href="#book"')
# the product login is a live app, unrelated to this page
body = body.replace('href="#login"', 'href="https://workspace.yantrailabs.com/"')

# ------------------------------------------------- "The agents" -> AI team
# Section label (nav, section eyebrow, footer column) and the footer's list of
# agent names, which now carry the same "<name> Agent" form as the ring chips.
assert body.count('The agents') == 3, body.count('The agents')
body = body.replace('The agents', 'The AI team')

_FOOTER_AGENTS = ['Duplicate', 'Pricing', '3-way match', 'Payments', 'Discount',
                  'GST', 'TDS', 'MSME']
_i = body.index('The AI team', body.index('id="footer"'))
_head, _tail = body[:_i], body[_i:]
for _name in _FOOTER_AGENTS:
    _needle = '>%s</a>' % _name
    assert _tail.count(_needle) == 1, (_name, _tail.count(_needle))
    _tail = _tail.replace(_needle, '>%s Agent</a>' % _name)
body = _head + _tail

# There is no customers page and no decision yet on which of the trust-band
# logos are AiFA customers rather than YantrAI's, so the link is dropped.
_i = body.index('>Customers</a>')
_start = body.rfind('<a ', 0, _i)
body = body[:_start] + body[_i + len('>Customers</a>'):]
assert '>Customers</a>' not in body

# --------------------------------------------------------------- cleanup --
body = re.sub(r' data-dc-tpl="\d+"', '', body)          # runtime bookkeeping
body = re.sub(r'<template[^>]*id="__bundler_thumbnail"[^>]*>.*?</template>', '', body, flags=re.S)
body = body.replace(' class="sc-interp"', '')            # interpolation wrappers
body = re.sub(r'\n[ \t]*\n[ \t]*\n+', '\n\n', body)

# semantic landmarks, cheap and safe: nav / main / footer are pure wrappers
assert 'data-nav="1"' in body and 'data-footer="1"' in body

# ------------------------------------------------------------ path prefix --
if BASE:
    _attr = r'(src|href|poster)="assets/'
    body = re.sub(_attr, r'\1="%sassets/' % BASE, body)
    head = re.sub(_attr, r'\1="%sassets/' % BASE, head)
    # absolute og:image / twitter:image
    head = head.replace(SITE_URL.rstrip('/') + '/assets/', SITE_URL.rstrip('/') + BASE + 'assets/')
    app_js = app_js.replace("'assets/aifa-agent-teams-60s.mp4'",
                            "'%sassets/aifa-agent-teams-60s.mp4'" % BASE)

# ------------------------------------------------------------- assemble ---
page = """<!DOCTYPE html>
<html lang="en">
<head>
%s
<style>
/* --- from the design canvas (helmet) --- */
%s
/* --- hover states generated by the canvas runtime --- */
%s
</style>
<link rel="stylesheet" href="%ssite.css">
</head>
<body>
<a class="visually-hidden" href="#how">Skip to content</a>
%s
<script src="%sapp.js" defer></script>
</body>
</html>
""" % (head.strip(), helmet_css.strip(), hover_css.strip(), BASE, body.strip(), BASE)

with open(os.path.join(ROOT, 'index.html'), 'w', encoding='utf-8') as f:
    f.write(page)
with open(os.path.join(ROOT, 'site.css'), 'w', encoding='utf-8') as f:
    f.write(site_css)
with open(os.path.join(ROOT, 'app.js'), 'w', encoding='utf-8') as f:
    f.write(app_js)

print('index.html %.1f KB' % (len(page) / 1024))
print('site.css   %.1f KB' % (len(site_css) / 1024))
print('app.js     %.1f KB' % (len(app_js) / 1024))
