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
ROOT_URL = BASE or '/'
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
        frag = re.sub(r'(src|href|poster)="(?!/|https?:|mailto:|data:|#)([^"]+)"',
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
    return """<!DOCTYPE html>
<html lang="en">
<head>
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
""" % (head_page.strip(), helmet_css.strip(), hover_css.strip(), ROOT_URL, ROOT_URL,
       extra_css, nav_abs, content, foot_abs, ROOT_URL)


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
    content = read('pages', slug + '.html')
    extra = CALC_JS if 'id="outflow"' in content else ''
    html_out = build_content_page(slug, page_title, description, content, NAV, FOOTER, extra)
    out_dir = os.path.join(ROOT, slug)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'index.html'), 'w', encoding='utf-8') as f:
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
AGENT_DATA = json.loads(read('pages', 'agents.json'))
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
    html_out = build_content_page(
        'agents/' + a['slug'],
        '%s — %s | AiFA' % (a['name'], a['statement'].rstrip('.')),
        a['statement'] + ' ' + a['mechanism'][:150],
        render_agent(a), NAV, FOOTER)
    d = os.path.join(ROOT, 'agents', a['slug'])
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html_out)
    page_count += 1

idx = build_content_page('agents', 'The AI team | AiFA',
                         'Every agent is one absolute commitment, enforced on every transaction.',
                         render_agents_index(), NAV, FOOTER)
os.makedirs(os.path.join(ROOT, 'agents'), exist_ok=True)
with open(os.path.join(ROOT, 'agents', 'index.html'), 'w', encoding='utf-8') as f:
    f.write(idx)
page_count += 1
print('%-18s %d agent pages + index' % ('agents/', len(AGENTS)))


# ------------------------------------------------ generated workflow pages
WORKFLOW_PAGES = json.loads(read('pages', 'workflows.json'))


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
    html_out = build_content_page(
        'workflows/' + w['slug'],
        '%s — %s | AiFA' % (w['name'], w['headline'].rstrip('.')),
        w['lede'][:200],
        render_workflow(w), NAV, FOOTER)
    d = os.path.join(ROOT, 'workflows', w['slug'])
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html_out)
    page_count += 1
print('%-18s %d pages' % ('workflows/', len(WORKFLOW_PAGES)))
print('total content pages: %d' % page_count)
