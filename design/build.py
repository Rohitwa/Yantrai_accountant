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
import html as html_lib
import os, re, json, html, shutil, sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SITE_URL = os.environ.get('AIFA_SITE_URL', 'https://yantrailabs.com')
# When the site is served from '/' but its files live under a sub-path (the
# App Engine layout puts them in /aifa/), local refs need that prefix.
BASE = os.environ.get('AIFA_BASE', '').strip('/')
BASE = ('/' + BASE + '/') if BASE else ''
ROOT_URL = BASE or '/'
# Assets are referenced absolutely from every page. The homepage used relative
# refs, which is fine at the root and breaks the moment it is also built at
# /fr/ — the same file, one directory down.
ASSET_BASE = BASE or '/'
# Where the savings-check form posts. Same-origin on App Engine.
FORM_ENDPOINT = os.environ.get('AIFA_FORM_ENDPOINT', '')

# --- locale -------------------------------------------------------------
# English builds to the root; every other locale builds to its own subtree.
# Assets, CSS and JS are shared and always live at the root, so only page
# links carry the prefix.
LOCALE = os.environ.get('AIFA_LOCALE', 'en').lower()
LOCALES = ('en', 'fr')
assert LOCALE in LOCALES, LOCALE
PAGE_PREFIX = '' if LOCALE == 'en' else '/' + LOCALE
OUT = ROOT if LOCALE == 'en' else os.path.join(ROOT, LOCALE)
SHARED = ('/assets/', '/site.css', '/page.css', '/app.js', '/api/',
          '/robots.txt', '/sitemap.xml')

LANG_NAMES = {'en': 'English', 'fr': 'Français'}

# Only pages that actually exist in this locale are built and linked. A /fr/
# URL serving English is worse than no /fr/ URL: the reader gets the wrong
# language and hreflang advertises a translation that is not one.
def _translated_paths():
    if LOCALE == 'en':
        return None                      # everything
    done = set()
    for name in ('index',):
        if os.path.exists(os.path.join(ROOT, 'pages', 'i18n', LOCALE + '.json')):
            done.add('')
    d = os.path.join(ROOT, 'pages', LOCALE)
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith('.html'):
                done.add(f[:-5])
    for src, prefix in (('agents', 'agents'), ('workflows', 'workflows'),
                        ('integrations', 'integrations'), ('roles', 'for')):
        f = os.path.join(ROOT, 'pages', src + '.' + LOCALE + '.json')
        if os.path.exists(f):
            data = json.loads(open(f, encoding='utf-8').read())
            items = data['agents'] if src == 'agents' else data
            done.add(prefix)
            for it in items:
                done.add(prefix + '/' + it['slug'])
    return done


TRANSLATED = _translated_paths()


def has_translation(slug):
    return TRANSLATED is None or slug in TRANSLATED
# marks a link that is already locale-correct, so localise_links leaves it alone
LANG_HREF = '\x00lang\x00'


def out_path(*parts):
    d = os.path.join(OUT, *parts[:-1]) if len(parts) > 1 else OUT
    os.makedirs(d, exist_ok=True)
    return os.path.join(OUT, *parts)


def localise_links(html_text):
    """Prefix internal page links with the locale. Assets stay at the root —
    they are identical across locales and duplicating them would double the
    payload for nothing."""
    if not PAGE_PREFIX:
        return html_text.replace(LANG_HREF, '')

    def fix(m):
        attr, url = m.group(1), m.group(2)
        if url.startswith(SHARED):
            return m.group(0)
        if url == '/':
            return '%s="%s/"' % (attr, PAGE_PREFIX)
        # a link to a page with no translation goes to the English one
        target = url.split('#')[0].strip('/')
        if not has_translation(target):
            return m.group(0)
        return '%s="%s%s"' % (attr, PAGE_PREFIX, url)

    html_text = re.sub(r'(href|src)="(/[^"]*)"', fix, html_text)
    return html_text.replace(LANG_HREF, '')


def lang_links(slug):
    """hreflang alternates plus the switcher, for one page. Slugs are the same
    in every locale, so the alternate is the same path under a prefix."""
    path = '/' + slug if slug else '/'
    alts = []
    for code in LOCALES:
        if code != 'en' and not has_translation(slug):
            continue
        pre = '' if code == 'en' else '/' + code
        alts.append('<link rel="alternate" hreflang="%s" href="%s%s%s">'
                    % (code, SITE_URL.rstrip('/'), pre, path))
    alts.append('<link rel="alternate" hreflang="x-default" href="%s%s">'
                % (SITE_URL.rstrip('/'), path))
    return '\n'.join(alts)


def lang_switcher(slug):
    path = '/' + slug if slug else '/'
    items = []
    for code in LOCALES:
        if code != 'en' and not has_translation(slug):
            continue
        pre = '' if code == 'en' else '/' + code
        if code == LOCALE:
            items.append('<span aria-current="true">%s</span>' % code.upper())
        else:
            items.append('<a href="%s%s%s" hreflang="%s" data-set-lang="%s">%s</a>'
                         % (LANG_HREF, pre, path, code, code, code.upper()))
    return ('<span data-lang-switch="1" aria-label="Language">%s</span>'
            % '<span aria-hidden="true">·</span>'.join(items))

MISSING_TRANSLATIONS = []


def read(*p):
    with open(os.path.join(ROOT, *p), encoding='utf-8') as f:
        return f.read()


def read_localised(*p):
    """Prefer the locale's source, fall back to English and record the gap so
    the build can report exactly what is still untranslated."""
    if LOCALE != 'en':
        parts = list(p)
        if parts[-1].endswith('.json'):
            cand = parts[:-1] + [parts[-1][:-5] + '.' + LOCALE + '.json']
        else:
            cand = parts[:-1] + [LOCALE, parts[-1]]
        if os.path.exists(os.path.join(ROOT, *cand)):
            return read(*cand)
        MISSING_TRANSLATIONS.append('/'.join(p))
    return read(*p)

body = read('_captures', 'w1440.html')
helmet_css = read('_captures', 'helmet.css')
hover_css = read('_captures', 'hover.css')
head = read('src', 'head.html').replace('__SITE_URL__', SITE_URL.rstrip('/'))
form = read('src', 'form.html')
site_css = read('src', 'site.css')
app_js = read('src', 'app.js').replace("var FORM_ENDPOINT = '';",
                                       "var FORM_ENDPOINT = '%s';" % FORM_ENDPOINT)


# ---------------------------------------------------------- page shell ----
VOID = {'img', 'br', 'input', 'hr', 'meta', 'link', 'source', 'area', 'base', 'col',
        'embed', 'param', 'track', 'wbr'}
TAG_RE = re.compile(r'<(/?)([a-zA-Z][\w-]*)([^>]*)>')


def extract_block(html, marker):
    """Return the balanced <div> element whose opening tag carries `marker`."""
    i = html.index(marker)
    start = html.rfind('<div', 0, i)
    depth, pos = 0, start
    while pos < len(html):
        m = TAG_RE.search(html, pos)
        if not m:
            break
        closing, name, attrs = m.group(1), m.group(2).lower(), m.group(3)
        if closing:
            depth -= 1
            if depth == 0:
                return html[start:m.end()]
        elif name not in VOID and not attrs.rstrip().endswith('/'):
            depth += 1
        pos = m.end()
    raise ValueError('unbalanced block for ' + marker)


def build_content_page(slug, page_title, description, content, nav, footer, extra_css=''):
    """Wrap a content fragment in the site shell. Homepage anchors become
    absolute so they still work from a sub-path."""
    # sub-pages live one directory down, so every root-relative ref in the
    # shared nav/footer has to become absolute
    def absolutise(frag):
        frag = re.sub(r'href="#([a-z-]+)"', r'href="/#\1"', frag)
        frag = re.sub(r'(src|href|poster)="(?!/|https?:|mailto:|data:|#|\x00)([^"]+)"',
                      lambda m: '%s="%s%s"' % (m.group(1), ROOT_URL, m.group(2)), frag)
        return frag

    nav_abs = absolutise(nav)
    foot_abs = absolutise(footer)
    head_page = (absolutise(head)
                 .replace('<title>AiFA — AI Teams for Finance | YantrAI</title>',
                          '<title>%s</title>' % page_title)
                 .replace(SITE_URL.rstrip('/') + '/">', SITE_URL.rstrip('/') + '/' + slug + '">'))
    head_page = re.sub(r'(<meta name="description" content=")[^"]*(")',
                       lambda m: m.group(1) + description + m.group(2), head_page, count=1)
    head_page = re.sub(r'(<meta property="og:title" content=")[^"]*(")',
                       lambda m: m.group(1) + page_title + m.group(2), head_page, count=1)
    head_page = re.sub(r'(<meta (?:property="og:description"|name="twitter:description") content=")[^"]*(")',
                       lambda m: m.group(1) + description + m.group(2), head_page)
    return localise_links("""<!DOCTYPE html>
<html lang="%s">
<head>
%s
%s
<style>
%s
%s
</style>
<link rel="stylesheet" href="%ssite.css">
<link rel="stylesheet" href="%spage.css">
%s
</head>
<body class="page">
<a class="visually-hidden" href="#main">Skip to content</a>
%s
<main id="main">
%s
</main>
%s
<script src="%sapp.js" defer></script>
</body>
</html>
""" % (LOCALE, head_page.strip(), lang_links(slug), helmet_css.strip(), hover_css.strip(),
       ROOT_URL, ROOT_URL, extra_css, nav_abs, content, foot_abs, ROOT_URL))


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
    lang_switcher('')      # shared nav; app.js retargets to the current path
    + '<button type="button" data-nav-toggle="1" aria-expanded="false" '
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

# ------------------------------------------------- research band (homepage)
# Sits between the integrations grid and the demo pitch: the case that this is
# where the category is going, before the ask.
RESEARCH_BAND = '''
  <div id="research" data-section="research" style="content-visibility: auto; contain-intrinsic-size: auto 700px; scroll-margin-top: 90px; background: #FFFFFF; border-top: 1px solid #E6EAE4; padding: 128px 56px; display: flex; justify-content: center">
    <div style="width: 100%; max-width: 1160px; display: flex; flex-direction: column">
      <span style="display: inline-flex; align-items: center; gap: 10px; font-size: 12.5px; font-weight: 600; letter-spacing: 0.12em; text-transform: uppercase; color: #5A5A5A">
        <span style="width: 7px; height: 7px; background: #EADC8F; display: block"></span>The evidence
      </span>
      <h2 data-research-h2="1" style="margin: 30px 0 0; max-width: 860px; font-family: Newsreader, Georgia, 'Times New Roman', serif; font-weight: 300; font-size: 44px; line-height: 1.08; letter-spacing: -0.03em; color: #151414; text-wrap: balance">Finance AI isn't stalling on models.<br>It's stalling on <span style="font-style: italic; background: linear-gradient(180deg, transparent 82%, #F9EBA6 82%, #F9EBA6 96%, transparent 96%)">governance and data</span>.</h2>
      <p style="margin: 26px 0 0; max-width: 620px; font-size: 17px; font-weight: 500; line-height: 1.55; letter-spacing: -0.51px; color: #5A5A5A; text-wrap: pretty">Three independent studies point the same way. None of the blockers they name is a model problem — which is why buying a better model has not moved the number.</p>

      <div data-research-grid="1" style="margin-top: 56px; display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; background: #E6EAE4; border: 1px solid #E6EAE4">
        <div style="background: #FFFFFF; padding: 30px 28px; display: flex; flex-direction: column; gap: 12px">
          <span style="font-family: Newsreader, Georgia, serif; font-weight: 300; font-size: 46px; line-height: 1; letter-spacing: -1.38px; color: #151414; font-variant-numeric: tabular-nums">59%</span>
          <span style="font-size: 15px; font-weight: 500; line-height: 1.45; letter-spacing: -0.45px; color: #5A5A5A">of finance departments use AI — against 58% a year earlier. A year of attention, one point of movement.</span>
          <span style="margin-top: auto; padding-top: 8px; font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; letter-spacing: -0.1px; color: #9A9A9A">Gartner · Nov 2025 · n=183</span>
        </div>
        <div style="background: #FFFFFF; padding: 30px 28px; display: flex; flex-direction: column; gap: 12px">
          <span style="font-family: Newsreader, Georgia, serif; font-weight: 300; font-size: 46px; line-height: 1; letter-spacing: -1.38px; color: #151414; font-variant-numeric: tabular-nums">1 in 3</span>
          <span style="font-size: 15px; font-weight: 500; line-height: 1.45; letter-spacing: -0.45px; color: #5A5A5A">organisations reach maturity on agentic governance and controls. Security and risk is the top barrier to scaling.</span>
          <span style="margin-top: auto; padding-top: 8px; font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; letter-spacing: -0.1px; color: #9A9A9A">McKinsey · State of AI Trust 2026</span>
        </div>
        <div style="background: #FFFFFF; padding: 30px 28px; display: flex; flex-direction: column; gap: 12px">
          <span style="font-family: Newsreader, Georgia, serif; font-weight: 300; font-size: 46px; line-height: 1; letter-spacing: -1.38px; color: #151414; font-variant-numeric: tabular-nums">3</span>
          <span style="font-size: 15px; font-weight: 500; line-height: 1.45; letter-spacing: -0.45px; color: #5A5A5A">infrastructure obstacles hold agents back in finance: legacy integration, data architecture, governance frameworks.</span>
          <span style="margin-top: auto; padding-top: 8px; font-family: ui-monospace, Menlo, monospace; font-size: 11.5px; letter-spacing: -0.1px; color: #9A9A9A">Deloitte · Tech Trends 2026</span>
        </div>
      </div>

      <div data-research-cta="1" style="margin-top: 40px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap">
        <a href="/research" style="display: inline-flex; align-items: center; gap: 10px; background: #FFFFFF; border: 1px solid #DCE0DA; border-radius: 10px; padding: 15px 22px; font-size: 15px; font-weight: 600; letter-spacing: -0.45px; color: #151414; text-decoration: none">Read the full argument<span>&#8594;</span></a>
        <span style="font-size: 14px; font-weight: 500; letter-spacing: -0.42px; color: #767676">Why AiFA is an ecosystem for deploying finance agents, not an AP tool</span>
      </div>
    </div>
  </div>
'''

_demo_i = body.index('id="demo"')
_demo_start = body.rfind('<div ', 0, _demo_i)
body = body[:_demo_start] + RESEARCH_BAND.strip() + '\n\n  ' + body[_demo_start:]

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

# ------------------------------------------------------------ brand lockup
# AiFA leads; YantrAI becomes the endorsement line. The mark is d4-seal from
# the brand kit (~/Desktop/memory/aifa_brand), not a redraw.
_nav_logo = re.search(
    r'<img[^>]*src="assets/yantrai_logo\.png"[^>]*height: 22px[^>]*>', body)
assert _nav_logo, 'nav logo'
body = body.replace(_nav_logo.group(0), (
    '<a href="/" data-brand="1" style="display: flex; align-items: center; gap: 11px; '
    'text-decoration: none;">'
    '<img src="assets/aifa-mark.svg" alt="" width="30" height="30" '
    'style="height: 30px; width: 30px; display: block;">'
    '<span style="display: flex; flex-direction: column; line-height: 1;">'
    '<span style="font-size: 19px; font-weight: 700; letter-spacing: -0.022em; '
    'color: #151414;">AiFA</span>'
    '<span data-brand-by="1" style="margin-top: 3px; font-size: 10.5px; font-weight: 500; '
    'letter-spacing: 0.02em; color: #9A9A9A;">by YantrAI</span>'
    '</span></a>'))

# the tagline block carried a duplicate "AiFA"; the lockup owns the name now
_old_tag = re.search(
    r'<span[^>]*font-size: 15px; font-weight: 700; letter-spacing: -0\.35px[^>]*>AiFA</span>',
    body)
assert _old_tag, 'nav tagline AiFA'
body = body.replace(_old_tag.group(0), '')

# the dark tile at the centre of the integrations grid led with YantrAI
_tile_logo = re.search(
    r'<img[^>]*src="assets/yantrai_logo\.png"[^>]*brightness\(0\) invert\(1\)[^>]*>', body)
assert _tile_logo, 'tile logo'
body = body.replace(_tile_logo.group(0),
                    '<img src="assets/aifa-mark-onDark.svg" alt="" width="30" height="30" '
                    'style="height: 30px; width: 30px; display: block;">')

# ------------------------------------------------------ bake footer links
# These were href="#" rewritten by app.js on load, which meant the whole
# internal link graph depended on a crawler executing JS. Now they ship
# resolved, and app.js only has to keep the genuinely-unrouted ones inert.
LINK_MAP = {
    'duplicate agent': '/agents/duplicate-agent',
    'pricing agent': '/agents/pricing-agent',
    '3-way match agent': '/agents/3-way-match-agent',
    'gst agent': '/agents/gst-agent',
    'tds agent': '/agents/tds-agent',
    'discount agent': '/agents/discount-agent',
    'payments agent': '/agents/payments-agent',
    'msme agent': '/agents/msme-agent',
    'view all agents': '/agents',
    'invoice entry': '/workflows/invoice-entry',
    'sync to erp': '/workflows/sync-to-erp',
    'payment processing': '/workflows/payment-processing',
    'daily close': '/workflows/daily-close',
    'multi-entity groups': '/workflows/multi-entity-groups',
    'multi-currency': '/workflows/multi-currency',
    'sap': '/integrations/sap',
    'oracle': '/integrations/oracle',
    'oracle netsuite': '/integrations/netsuite',
    'tally': '/integrations/tally',
    'zoho books': '/integrations/zoho-books',
    'quickbooks': '/integrations/quickbooks',
    'sage': '/integrations/sage',
    'odoo': '/integrations/odoo',
    'anything with an export': '/integrations',
    'cfo': '/for/cfo',
    'controller': '/for/controller',
    'head of finance': '/for/head-of-finance',
    'ap lead': '/for/ap-lead',
    'group treasurer': '/for/group-treasurer',
    'internal audit': '/for/internal-audit',
    'about yantrai labs': '/about',
    'careers': '/careers',
    'security': '/security',
    'contact': '/#book',
    'support': '/#book',
    'how aifa works': '/#how',
    'what it found': '/what-it-found',
    'implementation': '/integrations',
}

_baked, _left = 0, []


def _bake_anchor(m):
    """Resolve one <a href="#"> against LINK_MAP, by its visible label."""
    global _baked
    whole, inner = m.group(0), m.group(2)
    key = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', inner)).strip().lower()
    key = key.rstrip(' \u2192').strip()
    target = LINK_MAP.get(key)
    if not target:
        _left.append(key)
        return whole
    _baked += 1
    return (whole.replace('href="#"', 'href="%s"' % target)
                 .replace(' data-placeholder-link="1"', ''))


body = re.sub(r'(<a\b[^>]*href="#"[^>]*>)(.*?)(</a>)', _bake_anchor, body, flags=re.S)
print('footer links baked: %d   still inert: %s' % (_baked, sorted(set(_left)) or 'none'))

# --------------------------------------------------- homepage translation
# The homepage copy is baked into the design-canvas snapshot, so it is
# translated by replacing whole text nodes rather than by a template.
if os.environ.get('AIFA_DUMP_STRINGS'):
    _seen, _out = set(), []
    for _t in re.findall(r'>([^<>]+)<', re.sub(r'<(script|style)\b.*?</\1>', ' ', body, flags=re.S)):
        _k = html_lib.unescape(_t).strip()
        if _k and re.search(r'[A-Za-z]{2}', _k) and _k not in _seen:
            _seen.add(_k); _out.append(_k)
    with open(os.path.join(ROOT, 'pages', 'i18n', '_en_strings.json'), 'w', encoding='utf-8') as f:
        json.dump({k: '' for k in _out}, f, indent=1, ensure_ascii=False)
    print('dumped %d homepage strings' % len(_out))

if LOCALE != 'en':
    _i18n_file = os.path.join(ROOT, 'pages', 'i18n', LOCALE + '.json')
    if os.path.exists(_i18n_file):
        _strings = json.loads(open(_i18n_file, encoding='utf-8').read())
        _hit = 0

        def _translate_node(m):
            global _hit
            raw = m.group(1)
            key = html_lib.unescape(raw).strip()
            if key in _strings and _strings[key]:
                _hit += 1
                lead = raw[:len(raw) - len(raw.lstrip())]
                tail = raw[len(raw.rstrip()):]
                return '>' + lead + html_lib.escape(_strings[key], quote=False) + tail + '<'
            return m.group(0)

        body = re.sub(r'>([^<>]+)<', _translate_node, body)
        # the CTA chip is a bare rupee glyph — no word characters, so it never
        # reaches the string map, and it is the wrong currency outside India
        # bare currency glyphs carry no word characters, so they never reach the
        # string map — and the rupee is the wrong currency outside India
        body = body.replace('>\u20b9</span>', '>\u20ac</span>')

        _meta = _strings.get('_meta') or {}
        if _meta:
            head = re.sub(r'<title>[^<]*</title>', '<title>%s</title>' % _meta['title'], head, count=1)
            for attr, key in ((r'name="description"', 'description'),
                              (r'property="og:title"', 'og_title'),
                              (r'name="twitter:title"', 'og_title'),
                              (r'property="og:description"', 'og_description'),
                              (r'name="twitter:description"', 'og_description')):
                head = re.sub(r'(<meta %s content=")[^"]*(")' % attr,
                              lambda m: m.group(1) + _meta[key].replace('&', '&amp;').replace('"', '&quot;') + m.group(2),
                              head, count=1)
            head = head.replace('<link rel="canonical" href="%s/">' % SITE_URL.rstrip('/'),
                                '<link rel="canonical" href="%s/%s/">' % (SITE_URL.rstrip('/'), LOCALE))
        print('homepage: %d text nodes translated to %s' % (_hit, LOCALE))
    else:
        MISSING_TRANSLATIONS.append('pages/i18n/%s.json' % LOCALE)

# --------------------------------------------------------------- cleanup --
body = re.sub(r' data-dc-tpl="\d+"', '', body)          # runtime bookkeeping
body = re.sub(r'<template[^>]*id="__bundler_thumbnail"[^>]*>.*?</template>', '', body, flags=re.S)
body = body.replace(' class="sc-interp"', '')            # interpolation wrappers
body = re.sub(r'\n[ \t]*\n[ \t]*\n+', '\n\n', body)

# semantic landmarks, cheap and safe: nav / main / footer are pure wrappers
assert 'data-nav="1"' in body and 'data-footer="1"' in body

# ------------------------------------------------------------ path prefix --
_attr = r'(src|href|poster)="assets/'
body = re.sub(_attr, r'\1="%sassets/' % ASSET_BASE, body)
head = re.sub(_attr, r'\1="%sassets/' % ASSET_BASE, head)
if BASE:
    # absolute og:image / twitter:image
    head = head.replace(SITE_URL.rstrip('/') + '/assets/', SITE_URL.rstrip('/') + BASE + 'assets/')
app_js = app_js.replace("'assets/aifa-agent-teams-60s.mp4'",
                        "'%sassets/aifa-agent-teams-60s.mp4'" % ASSET_BASE)

# ------------------------------------------------------------- assemble ---
page = localise_links("""<!DOCTYPE html>
<html lang="%s">
<head>
%s
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
""" % (LOCALE, head.strip(), lang_links(''), helmet_css.strip(), hover_css.strip(),
       ASSET_BASE, body.strip(), ASSET_BASE))

with open(out_path('index.html'), 'w', encoding='utf-8') as f:
    f.write(page)

# ------------------------------------------------------- content pages ----
# The homepage comes out of the design canvas; these are hand-written and share
# its nav, footer and helmet CSS through the shell in build_content_page().
NAV = extract_block(body, 'data-nav="1"')
FOOTER = extract_block(body, 'data-footer="1"')

PAGES = [
    ('what-it-found', 'What it found — how ₹30 crore comes out of ₹1,000 crore | AiFA',
     'A ₹1,000 crore company loses about 3% of outflow across six ordinary failures. '
     'Here is the breakdown, and what each one is worth.'),
    ('security', 'Security — the PRISM-ES stack | AiFA',
     'Where your data sits, who can see it, what is retained, and what trains on it. '
     'Seven layers, read bottom-up.'),
    ('research', 'Finance AI is stalling on governance, not models | AiFA',
     'Adoption in finance functions has gone flat. Gartner, McKinsey and Deloitte point '
     'the same way — the blockers are data and governance, not model capability.'),
    ('about', 'About YantrAI Labs | AiFA',
     'We build AI teams for the work people can only ever spot-check. Vision, mission, '
     'what we believe, and who we are.'),
    ('careers', 'Careers — tell us what you\'d build | AiFA',
     'No posted roles. We hire people we cannot not hire. What the work is like, and a '
     'form that goes straight to a founder.'),
]

CALC_JS = '''<script>
document.addEventListener('DOMContentLoaded', function () {
  var input = document.getElementById('outflow'), out = document.getElementById('calc-out');
  if (!input || !out) return;
  function render() {
    var cr = parseFloat(input.value);
    out.textContent = isFinite(cr) && cr >= 0
      ? '\u20B9' + (cr * 0.03).toFixed(1) + ' Cr'
      : '\u2014';
  }
  input.addEventListener('input', render);
  render();
});
</script>'''

page_count = 0
for slug, page_title, description in PAGES:
    if not has_translation(slug):
        continue
    content = read_localised('pages', slug + '.html')
    extra = CALC_JS if 'id="outflow"' in content else ''
    html_out = build_content_page(slug, page_title, description, content, NAV, FOOTER, extra)
    with open(out_path(slug, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html_out)
    page_count += 1
    print('%-18s %.1f KB' % (slug + '/', len(html_out) / 1024))
with open(os.path.join(ROOT, 'site.css'), 'w', encoding='utf-8') as f:
    f.write(site_css)
with open(os.path.join(ROOT, 'app.js'), 'w', encoding='utf-8') as f:
    f.write(app_js)
shutil.copyfile(os.path.join(ROOT, 'src', 'page.css'), os.path.join(ROOT, 'page.css'))

print('index.html %.1f KB' % (len(page) / 1024))
print('site.css   %.1f KB' % (len(site_css) / 1024))
print('app.js     %.1f KB' % (len(app_js) / 1024))

# --------------------------------------------------- generated agent pages
AGENT_DATA = json.loads(read_localised('pages', 'agents.json'))
AGENTS = AGENT_DATA['agents']
WORKFLOWS = AGENT_DATA['workflows']
BY_SLUG = {a['slug']: a for a in AGENTS}


# agents that are named in the team but have no page written up yet — spelled
# out here because title-casing a slug turns PAN into "Pan"
UNWRITTEN = {
    'pan-agent': 'PAN Agent', 'msme-agent': 'MSME Agent', 'doa-agent': 'DOA Agent',
    'journal-agent': 'Journal Agent', 'reconciliation-agent': 'Reconciliation Agent',
    'intercompany-agent': 'Intercompany Agent', 'period-agent': 'Period Agent',
    'audit-agent': 'Audit Agent', 'report-agent': 'Report Agent',
    'payments-agent': 'Payments Agent', 'bank-agent': 'Bank Agent',
}


def agent_name(slug):
    a = BY_SLUG.get(slug)
    if a:
        return a['name']
    if slug in UNWRITTEN:
        return UNWRITTEN[slug]
    return slug.replace('-agent', '').replace('-', ' ').title() + ' Agent'


def agent_link(slug):
    if slug in BY_SLUG:
        return '<a href="/agents/%s">%s</a>' % (slug, agent_name(slug))
    # named but not yet written up
    return '<span>%s</span>' % agent_name(slug)


def li(items):
    return '\n'.join('        <li>%s</li>' % i for i in items)


def render_agent(a):
    up = a['upstream']
    down = a['downstream']
    hand = []
    if up:
        hand.append('<div><h3>Hands to it</h3><p>%s</p></div>'
                    % ', '.join(agent_link(s) for s in up))
    else:
        hand.append('<div><h3>Hands to it</h3><p>Nothing — this runs first, on the document as it arrives.</p></div>')
    hand.append('<div><h3>It triggers</h3><p>%s</p></div>'
                % (', '.join(agent_link(s) for s in down) if down else 'Nothing further in this workflow.'))

    others = [x for x in AGENTS if x['slug'] != a['slug']][:3]
    return """<div class="wrap">
  <div class="crumb"><a href="/">AiFA</a> · <a href="/agents">The AI team</a> · %(name)s</div>
  <div class="masthead">
    <span class="eyebrow %(accent)s"><i></i>%(workflow_name)s</span>
    <h1 class="display">%(name)s</h1>
    <p class="lede">%(lede)s</p>
  </div>
</div>

<section class="band">
  <div class="wrap">
    <p class="statement">%(statement)s<span class="after">%(mechanism)s</span></p>
  </div>
</section>

<section class="band wash">
  <div class="wrap">
    <h2 class="sec">What it checks</h2>
    <p>On every invoice, not a sample.</p>
    <ul class="checks">
%(checks)s
    </ul>
  </div>
</section>

<section class="band">
  <div class="wrap">
    <h2 class="sec">What it reads, what it writes</h2>
    <div class="cards">
      <div>
        <h3>Reads</h3>
        <p>%(reads)s</p>
      </div>
      <div>
        <h3>Writes</h3>
        <p>%(writes)s</p>
      </div>
    </div>

    <h2 class="sec" style="margin-top:56px">Where it sits in the team</h2>
    <p>Agents do not work alone. Each one hands its result to the next, so a finding raised here shows up as context downstream rather than being re-derived.</p>
    <div class="cards">
      %(handoffs)s
    </div>
  </div>
</section>

<section class="band wash">
  <div class="wrap">
    <h2 class="sec">When it isn't sure</h2>
    <p>%(ambiguity)s</p>
    <div class="callout">
      <span class="label">Why this matters more than the accuracy number</span>
      <p>Any check that runs on every transaction will meet cases it cannot settle. What makes a control trustworthy is not that it never hesitates — it is that hesitation produces a named question for a person, rather than a silent pass or a silent block.</p>
    </div>
  </div>
</section>

<section class="next">
  <div class="wrap">
    <h2>See what this one finds in your last 90 days.</h2>
    <p>One export. No connector. We come back within a working day.</p>
    <a class="btn" href="/#book"><span class="rupee">\u20B9</span>Check your savings<span>\u2192</span></a>
    <div class="inline-links">
      %(others)s
      <a href="/agents">All of the team \u2192</a>
    </div>
  </div>
</section>
""" % {
        'name': a['name'],
        'accent': a['accent'],
        'workflow_name': WORKFLOWS[a['workflow']],
        'lede': a['lede'],
        'statement': a['statement'],
        'mechanism': a['mechanism'],
        'checks': li(a['checks']),
        'reads': ' · '.join(a['reads']),
        'writes': ' · '.join(a['writes']),
        'handoffs': '\n      '.join(hand),
        'ambiguity': a['ambiguity'],
        'others': '\n      '.join('<a href="/agents/%s">%s \u2192</a>' % (o['slug'], o['name']) for o in others),
    }


def render_agents_index():
    groups = {}
    for a in AGENTS:
        groups.setdefault(a['workflow'], []).append(a)
    blocks = []
    for wf, label in WORKFLOWS.items():
        items = groups.get(wf, [])
        if not items:
            continue
        cards = '\n      '.join(
            '<div><h3><a href="/agents/%s" style="color:inherit;text-decoration:none">%s</a></h3>'
            '<p>%s</p></div>' % (a['slug'], a['name'], a['statement'])
            for a in items)
        blocks.append(
            '<h2 class="sec" style="margin-top:48px"><a href="/workflows/%s" '
            'style="color:inherit;text-decoration:none">%s</a></h2>\n'
            '    <div class="cards">\n      %s\n    </div>' % (wf, label, cards))

    return """<div class="wrap">
  <div class="crumb"><a href="/">AiFA</a> · The AI team</div>
  <div class="masthead">
    <span class="eyebrow iris"><i></i>The AI team</span>
    <h1 class="display">Every agent is one<br>absolute <em class="mark">commitment</em>.</h1>
    <p class="lede">Not a feature list. Each agent enforces a single rule on every transaction, and hands its result to the next one. These six are the ones behind the \u20B930 crore arithmetic; the rest of the team runs across receivables, treasury and the close.</p>
  </div>
</div>

<section class="band">
  <div class="wrap wrap-wide">
    %(blocks)s
  </div>
</section>

<section class="next">
  <div class="wrap">
    <h2>Put the team on your last 90 days.</h2>
    <a class="btn" href="/#book"><span class="rupee">\u20B9</span>Check your savings<span>\u2192</span></a>
    <div class="inline-links">
      <a href="/what-it-found">What the checks find \u2192</a>
      <a href="/security">How your data is handled \u2192</a>
    </div>
  </div>
</section>
""" % {'blocks': '\n\n    '.join(blocks)}


for a in AGENTS:
    if not has_translation('agents/' + a['slug']):
        continue
    html_out = build_content_page(
        'agents/' + a['slug'],
        '%s — %s | AiFA' % (a['name'], a['statement'].rstrip('.')),
        a['statement'] + ' ' + a['mechanism'][:150],
        render_agent(a), NAV, FOOTER)
    with open(out_path('agents', a['slug'], 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html_out)
    page_count += 1

idx = has_translation('agents') and build_content_page('agents', 'The AI team | AiFA',
                         'Every agent is one absolute commitment, enforced on every transaction.',
                         render_agents_index(), NAV, FOOTER)
if idx:
    with open(out_path('agents', 'index.html'), 'w', encoding='utf-8') as f:
        f.write(idx)
    page_count += 1
print('%-18s %d agent pages + index' % ('agents/', len(AGENTS)))


# ------------------------------------------------ generated workflow pages
WORKFLOW_PAGES = json.loads(read_localised('pages', 'workflows.json'))


def render_workflow(w):
    rows = []
    for slug, does in w['agents']:
        rows.append('          <tr><td>%s</td><td>%s</td></tr>' % (agent_link(slug), does))
    others = [x for x in WORKFLOW_PAGES if x['slug'] != w['slug']]
    return """<div class="wrap">
  <div class="crumb"><a href="/">AiFA</a> · Workflows · %(name)s</div>
  <div class="masthead">
    <span class="eyebrow %(accent)s"><i></i>%(name)s</span>
    <h1 class="display">%(headline)s</h1>
    <p class="lede">%(lede)s</p>
  </div>
</div>

<section class="band">
  <div class="wrap wrap-wide">
    <h2 class="sec">The same step, two ways</h2>
    <div class="cards">
      <div>
        <h3>How it runs today</h3>
        <ul class="checks sand" style="margin-top:16px">
%(today)s
        </ul>
      </div>
      <div>
        <h3>With the team on it</h3>
        <ul class="checks" style="margin-top:16px">
%(withteam)s
        </ul>
      </div>
    </div>
  </div>
</section>

<section class="band wash">
  <div class="wrap wrap-wide">
    <h2 class="sec">Who runs, and what they enforce</h2>
    <p>All of them, on the same %(task_lower)s, at the same time. Not a queue.</p>
    <div class="tablewrap">
      <table class="grid">
        <thead><tr><th>Agent</th><th>What it enforces</th></tr></thead>
        <tbody>
%(rows)s
        </tbody>
      </table>
    </div>
    <p class="note" style="margin-top:20px;font-size:13.5px;color:var(--mute)">Agents without a link yet are part of the team but do not have a page written up.</p>
  </div>
</section>

<section class="band">
  <div class="wrap">
    <h2 class="sec">What lands in your ERP</h2>
    <p>%(lands)s</p>
    <div class="callout">
      <span class="label">%(stamp)s</span>
      <p>The stamp on a <strong>%(task_lower)s</strong> that cleared this step. It means every check ran and passed — not that a sample did, and not that it will be reviewed later.</p>
    </div>
  </div>
</section>

<section class="next">
  <div class="wrap">
    <h2>See this step run on your last 90 days.</h2>
    <p>One export. No connector. We come back within a working day.</p>
    <a class="btn" href="/#book"><span class="rupee">\u20B9</span>Check your savings<span>\u2192</span></a>
    <div class="inline-links">
      %(others)s
      <a href="/agents">The AI team \u2192</a>
    </div>
  </div>
</section>
""" % {
        'name': w['name'],
        'accent': w['accent'],
        'headline': w['headline'],
        'lede': w['lede'],
        'today': li(w['today']),
        'withteam': li(w['withteam']),
        'rows': '\n'.join(rows),
        'task_lower': w['task'].lower(),
        'lands': w['lands'],
        'stamp': w['stamp'],
        'others': '\n      '.join('<a href="/workflows/%s">%s \u2192</a>' % (o['slug'], o['name']) for o in others),
    }


for w in WORKFLOW_PAGES:
    if not has_translation('workflows/' + w['slug']):
        continue
    html_out = build_content_page(
        'workflows/' + w['slug'],
        '%s — %s | AiFA' % (w['name'], w['headline'].rstrip('.')),
        w['lede'][:200],
        render_workflow(w), NAV, FOOTER)
    with open(out_path('workflows', w['slug'], 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html_out)
    page_count += 1
print('%-18s %d pages' % ('workflows/', len(WORKFLOW_PAGES)))


# --------------------------------------------- generated integration pages
INTEGRATIONS = json.loads(read_localised('pages', 'integrations.json'))


def render_integration(x):
    others = [o for o in INTEGRATIONS if o['slug'] != x['slug']][:5]
    return """<div class="wrap">
  <div class="crumb"><a href="/">AiFA</a> · <a href="/integrations">Integrations</a> · %(name)s</div>
  <div class="masthead">
    <span class="eyebrow %(accent)s"><i></i>%(name)s</span>
    <h1 class="display">%(headline)s</h1>
    <p class="lede">%(lede)s</p>
  </div>
</div>

<section class="band">
  <div class="wrap wrap-wide">
    <h2 class="sec">What AiFA reads, what it writes back</h2>
    <div class="cards">
      <div>
        <h3>Reads from %(name)s</h3>
        <ul class="checks" style="margin-top:16px">
%(reads)s
        </ul>
      </div>
      <div>
        <h3>Writes back into %(name)s</h3>
        <ul class="checks" style="margin-top:16px">
%(writes)s
        </ul>
      </div>
    </div>
    <div class="callout">
      <span class="label">What it never touches</span>
      <p>%(never)s</p>
    </div>
  </div>
</section>

<section class="band wash">
  <div class="wrap">
    <h2 class="sec">Where you start</h2>
    <p class="statement">An export, not a project.<span class="after">%(start)s</span></p>
    <p>%(note)s</p>
  </div>
</section>

<section class="band">
  <div class="wrap wrap-wide">
    <h2 class="sec">What runs against it</h2>
    <p>The same team, whichever system holds the books. The checks depend on the contract, the PO and the receipt — not on one ERP\u2019s schema.</p>
    <div class="inline-links">
      <a href="/agents">All 17 agents \u2192</a>
      <a href="/workflows/invoice-entry">Invoice entry \u2192</a>
      <a href="/workflows/payment-processing">Payment processing \u2192</a>
      <a href="/security">How your data is handled \u2192</a>
    </div>
  </div>
</section>

<section class="next">
  <div class="wrap">
    <h2>Run it on 90 days of your own %(name)s data.</h2>
    <p>One export. No connector, and nothing to install.</p>
    <a class="btn" href="/#book"><span class="rupee">\u20B9</span>Check your savings<span>\u2192</span></a>
    <div class="inline-links">
      %(others)s
    </div>
  </div>
</section>
""" % {
        'name': x['name'], 'accent': x['accent'], 'headline': x['headline'], 'lede': x['lede'],
        'reads': li(x['reads']), 'writes': li(x['writes']),
        'never': x['never'], 'start': x['start'], 'note': x['note'],
        'others': '\n      '.join('<a href="/integrations/%s">%s \u2192</a>' % (o['slug'], o['name'])
                                  for o in others),
    }


def render_integrations_index():
    cards = '\n      '.join(
        '<div><h3><a href="/integrations/%s" style="color:inherit;text-decoration:none">%s</a></h3>'
        '<p>%s</p></div>' % (x['slug'], x['name'], x['headline']) for x in INTEGRATIONS)
    return """<div class="wrap">
  <div class="crumb"><a href="/">AiFA</a> · Integrations</div>
  <div class="masthead">
    <span class="eyebrow"><i></i>Integrations</span>
    <h1 class="display">Your ERP stays<br>the system of <em class="mark">record</em>.</h1>
    <p class="lede">AiFA reads from the system that already holds your books and posts outcomes back into it. There is no migration, no second ledger, and nothing to reconcile between the two.</p>
  </div>
</div>

<section class="band">
  <div class="wrap wrap-wide">
    <h2 class="sec">Where the books already live</h2>
    <div class="cards">
      %(cards)s
    </div>
    <div class="callout">
      <span class="label">Anything with an export</span>
      <p>The list above is where we have done the work of knowing the data model. It is not a gate. The savings check runs on a purchase register or an AP extract, and every ERP produces one — including the one you built yourself.</p>
    </div>
  </div>
</section>

<section class="next">
  <div class="wrap">
    <h2>Start with the export your team already produces.</h2>
    <a class="btn" href="/#book"><span class="rupee">\u20B9</span>Check your savings<span>\u2192</span></a>
    <div class="inline-links">
      <a href="/security">How your data is handled \u2192</a>
      <a href="/agents">The AI team \u2192</a>
    </div>
  </div>
</section>
""" % {'cards': cards}


for x in INTEGRATIONS:
    if not has_translation('integrations/' + x['slug']):
        continue
    html_out = build_content_page(
        'integrations/' + x['slug'],
        '%s + AiFA — %s | AiFA' % (x['name'], x['headline'].rstrip('.')),
        x['lede'][:200], render_integration(x), NAV, FOOTER)
    with open(out_path('integrations', x['slug'], 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html_out)
    page_count += 1

_idx = has_translation('integrations') and build_content_page('integrations', 'Integrations — your ERP stays the system of record | AiFA',
                          'AiFA reads from the ERP that already holds your books and posts back into it. '
                          'Tally, SAP, Oracle, NetSuite, Zoho Books, QuickBooks, Sage, Odoo.',
                          render_integrations_index(), NAV, FOOTER)
if _idx:
    with open(out_path('integrations', 'index.html'), 'w', encoding='utf-8') as f:
        f.write(_idx)
    page_count += 1
print('%-18s %d pages + index' % ('integrations/', len(INTEGRATIONS)))


# ---------------------------------------------------- generated role pages
ROLES = json.loads(read_localised('pages', 'roles.json'))


def render_role(r):
    others = [o for o in ROLES if o['slug'] != r['slug']]
    asks = '\n'.join('      <div class="step"><div class="n">%02d</div><div><h3>%s</h3></div></div>'
                     % (i + 1, q) for i, q in enumerate(r['asks']))
    return """<div class="wrap">
  <div class="crumb"><a href="/">AiFA</a> · <a href="/for">Roles</a> · %(name)s</div>
  <div class="masthead">
    <span class="eyebrow %(accent)s"><i></i>For the %(name)s</span>
    <h1 class="display">%(headline)s</h1>
    <p class="lede">%(lede)s</p>
  </div>
</div>

<section class="band">
  <div class="wrap wrap-wide">
    <h2 class="sec">What you are measured on</h2>
    <ul class="checks">
%(measured)s
    </ul>
    <div class="callout">
      <span class="label">Where the leak hits your number</span>
      <p>%(leak)s</p>
    </div>
  </div>
</section>

<section class="band wash">
  <div class="wrap">
    <h2 class="sec">What changes in your week</h2>
    <ul class="checks">
%(changes)s
    </ul>
  </div>
</section>

<section class="band">
  <div class="wrap">
    <h2 class="sec">What you would ask in the first meeting</h2>
    <div class="flow">
%(asks)s
    </div>
    <p class="statement" style="margin-top:34px">%(answer)s</p>
  </div>
</section>

<section class="next">
  <div class="wrap">
    <h2>Ninety days of invoices answers this better than a meeting.</h2>
    <p>One export, findings back within a working day, with the invoice attached to each.</p>
    <a class="btn" href="/#book"><span class="rupee">\u20B9</span>Check your savings<span>\u2192</span></a>
    <div class="inline-links">
      %(others)s
    </div>
  </div>
</section>
""" % {
        'name': r['name'], 'accent': r['accent'], 'headline': r['headline'], 'lede': r['lede'],
        'measured': li(r['measured']), 'changes': li(r['changes']),
        'leak': r['leak'], 'asks': asks, 'answer': r['answer'],
        'others': '\n      '.join('<a href="/for/%s">For the %s \u2192</a>' % (o['slug'], o['name'])
                                  for o in others),
    }


def render_roles_index():
    cards = '\n      '.join(
        '<div><h3><a href="/for/%s" style="color:inherit;text-decoration:none">%s</a></h3>'
        '<p>%s</p></div>' % (r['slug'], r['name'], r['headline']) for r in ROLES)
    return """<div class="wrap">
  <div class="crumb"><a href="/">AiFA</a> · Roles</div>
  <div class="masthead">
    <span class="eyebrow rose"><i></i>By role</span>
    <h1 class="display">The same product,<br>argued for the person<br>who has to <em class="mark">sign</em>.</h1>
    <p class="lede">A CFO, a controller and an AP lead are not buying the same thing. Same six leaks, same seventeen agents — different reason to care.</p>
  </div>
</div>

<section class="band">
  <div class="wrap wrap-wide">
    <div class="cards">
      %(cards)s
    </div>
  </div>
</section>

<section class="next">
  <div class="wrap">
    <h2>Whoever you are, it starts the same way.</h2>
    <a class="btn" href="/#book"><span class="rupee">\u20B9</span>Check your savings<span>\u2192</span></a>
    <div class="inline-links">
      <a href="/what-it-found">What the checks find \u2192</a>
      <a href="/security">How your data is handled \u2192</a>
    </div>
  </div>
</section>
""" % {'cards': cards}


for r in ROLES:
    if not has_translation('for/' + r['slug']):
        continue
    html_out = build_content_page(
        'for/' + r['slug'],
        'AiFA for the %s — %s | AiFA' % (r['name'], r['headline'].rstrip('.')),
        r['lede'][:200], render_role(r), NAV, FOOTER)
    with open(out_path('for', r['slug'], 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html_out)
    page_count += 1

_ridx = has_translation('for') and build_content_page('for', 'AiFA by role — CFO, Controller, AP Lead | AiFA',
                           'Same six leaks, same seventeen agents, different reason to care. '
                           'AiFA for the CFO, controller, head of finance, AP lead, treasurer and internal audit.',
                           render_roles_index(), NAV, FOOTER)
if _ridx:
    with open(out_path('for', 'index.html'), 'w', encoding='utf-8') as f:
        f.write(_ridx)
    page_count += 1
print('%-18s %d pages + index' % ('for/', len(ROLES)))
print('total content pages: %d' % page_count)
if LOCALE != 'en':
    if MISSING_TRANSLATIONS:
        print('UNTRANSLATED (%d, falling back to English): %s'
              % (len(MISSING_TRANSLATIONS), ', '.join(sorted(set(MISSING_TRANSLATIONS)))))
    else:
        print('all sources translated for %s' % LOCALE)
