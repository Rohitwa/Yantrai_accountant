/* YantrAI Web — the lead inbox, opened inside the YantrAI platform.

   The platform opens this page in an iframe as /admin?token=<its sign-in token>.
   That token is swapped once for a session of this app (POST /admin/api/session),
   removed from the address bar at once, and every later call carries the session
   as an Authorization header. No cookies: in the platform's iframe they would be
   third-party cookies, which browsers block.

   Everything a visitor typed into the website's forms is untrusted. It is only
   ever written with textContent / property setters, never parsed as HTML, and
   the page's CSP allows no inline script. */
(function () {
  'use strict';

  var SESSION_KEY = 'yw-session';
  var SLUG = 'yantrai-web';
  var STATUSES = { new: 'New', contacted: 'Contacted', qualified: 'Qualified', closed: 'Closed', spam: 'Spam' };
  var FORMS = { savings_check: 'Demo request', careers: 'Careers' };
  var NOTIFY = { mail_sent: 'Website email sent', mail_failed: 'Website email failed',
                 mail_disabled: 'No website email (emails are off)' };

  function $(sel) { return document.querySelector(sel); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }

  // ---- the platform, for "Home" and for reopening after a session ends ----
  var platform = (function () {
    var meta = document.querySelector('meta[name="yw-platform"]');
    var v = meta ? meta.getAttribute('content') : '';
    return /^https:\/\/[a-z0-9.-]+(:\d+)?$/i.test(v || '') || /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(v || '')
      ? v : '';
  })();
  // The platform mints a fresh sign-in only when it loads the app anew. A link to
  // its own current address (#remote-yantrai-web) would be a same-page jump that
  // mints nothing, so the link carries a query that is new on every click.
  function reopenUrl() {
    return platform ? platform + '/?yw=' + Date.now() + '#remote-' + SLUG : '';
  }

  // ---- 1. take the platform's token out of the address bar before anything else ----
  var platformToken = null;
  try {
    var params = new URLSearchParams(window.location.search);
    platformToken = params.get('token');
    if (params.has('token')) {
      window.history.replaceState(null, '', window.location.pathname + window.location.hash);
    }
  } catch (e) { /* an old browser: the server still refuses a reused token */ }

  var state = {
    session: null, user: '', leads: [], cursor: null, selected: null, detail: null,
    filters: { q: '', status: '', form: '' }, listSeq: 0, detailSeq: 0, busy: false
  };

  function saveSession(s) {
    state.session = s;
    try {
      if (s) sessionStorage.setItem(SESSION_KEY, JSON.stringify(s));
      else sessionStorage.removeItem(SESSION_KEY);
    } catch (e) { /* storage blocked: the session lives in memory only */ }
  }
  function loadSession() {
    try {
      var s = JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null');
      if (s && typeof s.token === 'string' && typeof s.expires_at === 'number' && s.expires_at * 1000 > Date.now()) return s;
    } catch (e) { /* ignore */ }
    return null;
  }

  // ---- messages ----
  var noticeTimer = null;
  function notice(text, isError) {
    var n = $('[data-notice]');
    n.textContent = text;
    n.classList.toggle('is-error', !!isError);
    n.hidden = !text;
    clearTimeout(noticeTimer);
    if (text && !isError) noticeTimer = setTimeout(function () { n.hidden = true; }, 5000);
  }

  function gate(title, text, showLink) {
    $('[data-inbox]').hidden = true;
    $('[data-export]').hidden = true;
    $('[data-signout]').hidden = true;
    $('[data-user]').hidden = true;
    $('[data-gate]').hidden = false;
    $('[data-gate-title]').textContent = title;
    $('[data-gate-text]').textContent = text || '';
    var link = $('[data-gate-link]');
    link.hidden = !(showLink && platform);
    if (platform) link.href = reopenUrl();
  }

  function sessionEnded(text) {
    saveSession(null);
    gate('Your session has ended',
         text || 'For your security, YantrAI Web signs you out after two hours. Open it again from the platform to carry on.',
         true);
  }

  // ---- the API ----
  function api(method, path, body) {
    var headers = { 'Accept': 'application/json' };
    if (state.session) headers['Authorization'] = 'Bearer ' + state.session.token;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    return fetch(path, {
      method: method, headers: headers, cache: 'no-store', credentials: 'omit',
      body: body === undefined ? undefined : JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (r.status === 401) { sessionEnded(data && data.error); throw { handled: true }; }
        if (!r.ok) throw { status: r.status, error: (data && data.error) || 'Something went wrong' };
        return data;
      });
    });
  }
  function failed(err) {
    if (err && err.handled) return;
    notice((err && err.error) || 'Could not reach the inbox. Check your connection and try again.', true);
  }

  // ---- formatting ----
  function when(iso, long) {
    var d = new Date(iso);
    if (isNaN(d)) return '';
    var now = new Date();
    var time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    var day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var diff = Math.round((today - day) / 86400000);
    if (!long) {
      if (diff === 0) return 'Today ' + time;
      if (diff === 1) return 'Yesterday';
      if (diff > 1 && diff < 7) return d.toLocaleDateString([], { weekday: 'short' });
      return d.toLocaleDateString([], { day: 'numeric', month: 'short', year: d.getFullYear() === now.getFullYear() ? undefined : 'numeric' });
    }
    return d.toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' }) + ', ' + time;
  }
  function statusPill(s) {
    return el('span', 'pill st-' + (STATUSES[s] ? s : 'closed'), STATUSES[s] || s);
  }
  function bytes(n) {
    if (typeof n !== 'number') return '';
    return n < 1024 * 1024 ? Math.max(1, Math.round(n / 1024)) + ' KB' : (n / 1048576).toFixed(1) + ' MB';
  }
  // a plain address the mail client can take as-is; anything else gets no link
  var PLAIN_EMAIL = /^[A-Za-z0-9.!#$%&'*+\/=^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$/;

  // ---- counts and list ----
  function renderCounts(c) {
    if (!c) return;
    $('[data-count="new"]').textContent = c.new;
    $('[data-count="week"]').textContent = c.week;
    $('[data-count="qualified"]').textContent = c.qualified;
    $('[data-count="all"]').textContent = c.all;
  }

  function rowFor(lead) {
    var li = el('li');
    var b = el('button', 'row');
    b.type = 'button';
    b.setAttribute('data-id', lead.id);
    b.setAttribute('aria-current', state.selected === lead.id ? 'true' : 'false');
    b.appendChild(el('span', 'c-name', lead.name));
    b.appendChild(el('span', 'sub', lead.company || FORMS[lead.form] || lead.form));
    b.appendChild(el('span', 'when', when(lead.received_at)));
    var st = el('span', 'c-status');
    st.appendChild(statusPill(lead.status));
    b.appendChild(st);
    b.addEventListener('click', function () { openLead(lead.id); });
    li.appendChild(b);
    return li;
  }

  function renderList() {
    var ul = $('[data-list]');
    ul.textContent = '';
    state.leads.forEach(function (lead) { ul.appendChild(rowFor(lead)); });
    $('[data-empty]').hidden = state.leads.length > 0;
    $('[data-more]').hidden = !state.cursor;
  }

  // filters travel in the request body, never the URL: search terms are often a
  // lead's name or email, and URLs end up in the server's request logs
  function filters(extra) {
    var f = { q: state.filters.q, status: state.filters.status, form: state.filters.form };
    if (extra) Object.keys(extra).forEach(function (k) { f[k] = extra[k]; });
    return f;
  }

  function loadLeads(more) {
    var seq = ++state.listSeq;
    var btn = $('[data-more]');
    btn.disabled = true;
    return api('POST', '/admin/api/leads/search', filters(more && state.cursor ? { before: state.cursor } : null))
      .then(function (data) {
        if (seq !== state.listSeq) return;          // a newer search has started
        state.leads = more ? state.leads.concat(data.leads) : data.leads;
        state.cursor = data.next || null;
        renderCounts(data.counts);
        renderList();
      })
      .catch(failed)
      .then(function () { btn.disabled = false; });
  }

  // ---- one lead ----
  function field(dl, label, value) {
    if (value === null || value === undefined || value === '') return;
    dl.appendChild(el('dt', null, label));
    dl.appendChild(el('dd', null, value));
  }

  function renderDetail(d) {
    var lead = d.lead;
    state.detail = lead;
    $('[data-detail-empty]').hidden = true;
    $('[data-detail-body]').hidden = false;
    $('.panes').classList.add('is-reading');
    $('[data-d-name]').textContent = lead.name;
    var st = $('[data-d-status]');
    st.textContent = '';
    st.appendChild(statusPill(lead.status));

    var dl = $('[data-d-fields]');
    dl.textContent = '';
    field(dl, 'Form', FORMS[lead.form] || lead.form);
    field(dl, 'Received', when(lead.received_at, true));
    field(dl, 'Email', lead.email);
    field(dl, 'Company', lead.company);
    field(dl, 'Role', lead.role);
    field(dl, 'ERP', lead.erp);
    field(dl, 'Annual outflow', lead.outflow);
    field(dl, 'LinkedIn', lead.linkedin);
    field(dl, 'Their work', lead.work);
    field(dl, 'Area', lead.area);
    if (lead.cv_filename) field(dl, 'CV', lead.cv_filename + (lead.cv_bytes ? ' (' + bytes(lead.cv_bytes) + ')' : '') + ' — sent by email only');
    field(dl, 'Page', lead.page);
    field(dl, 'Language', lead.locale === 'fr' ? 'French' : lead.locale === 'en' ? 'English' : lead.locale);

    var wrap = $('[data-d-note-wrap]');
    wrap.hidden = !lead.note;
    $('[data-d-note-label]').textContent = lead.form === 'careers' ? 'What they would want to own' : 'Their note';
    $('[data-d-note]').textContent = lead.note || '';

    var reply = $('[data-d-reply]');
    if (PLAIN_EMAIL.test(lead.email || '')) {
      var subject = lead.form === 'careers' ? 'Your application to YantrAI' : 'Your YantrAI demo request';
      // the address is percent-encoded (it may hold % & ? #); @ stays readable
      reply.href = 'mailto:' + encodeURIComponent(lead.email).replace('%40', '@') +
                   '?subject=' + encodeURIComponent(subject);
      reply.hidden = false;
    } else {
      reply.removeAttribute('href');
      reply.hidden = true;
    }

    $('#yw-set-status').value = STATUSES[lead.status] ? lead.status : 'new';
    $('#yw-status-note').value = '';

    var ul = $('[data-d-history]');
    ul.textContent = '';
    var items = [];
    (d.history || []).forEach(function (h) {
      var li = el('li');
      var who = h.actor || 'A platform admin';
      li.appendChild(el('span', null, who + ' changed the status from ' + (STATUSES[h.from] || h.from || '—') +
                                       ' to ' + (STATUSES[h.to] || h.to)));
      if (h.note) li.appendChild(el('span', 'hnote', h.note));
      li.appendChild(el('span', 'when', when(h.at, true)));
      items.push({ at: h.at, li: li });
    });
    (d.notify || []).forEach(function (n) {
      var li = el('li');
      li.appendChild(el('span', null, NOTIFY[n.event] || n.event));
      li.appendChild(el('span', 'when', when(n.at, true)));
      items.push({ at: n.at, li: li });
    });
    var rec = el('li');
    rec.appendChild(el('span', null, 'Received from the website'));
    rec.appendChild(el('span', 'when', when(lead.received_at, true)));
    items.push({ at: lead.received_at, li: rec });
    items.sort(function (a, b) { return new Date(b.at) - new Date(a.at); });
    items.forEach(function (i) { ul.appendChild(i.li); });
  }

  function openLead(id) {
    state.selected = id;
    Array.prototype.forEach.call(document.querySelectorAll('.row'), function (r) {
      r.setAttribute('aria-current', r.getAttribute('data-id') === id ? 'true' : 'false');
    });
    var seq = ++state.detailSeq;
    return api('GET', '/admin/api/leads/' + encodeURIComponent(id))
      .then(function (d) { if (seq === state.detailSeq) renderDetail(d); })
      .catch(failed);
  }

  function saveStatus(ev) {
    ev.preventDefault();
    var lead = state.detail;
    if (!lead || state.busy) return;
    var status = $('#yw-set-status').value;
    var note = $('#yw-status-note').value.trim();
    if (status === lead.status && !note) { notice('Nothing to save: pick a different status or add a note.'); return; }
    state.busy = true;
    var btn = ev.target.querySelector('button[type="submit"]');
    btn.disabled = true;
    api('POST', '/admin/api/leads/' + encodeURIComponent(lead.id) + '/status',
        { status: status, note: note, expected: lead.status })
      .then(function (res) {
        notice('Saved: ' + lead.name + ' is now ' + STATUSES[res.status] + '.');
        state.leads.forEach(function (l) { if (l.id === lead.id) l.status = res.status; });
        renderCounts(res.counts);
        renderList();
        return openLead(lead.id);
      })
      .catch(function (err) {
        if (err && err.status === 409) {
          // nothing was saved: show the latest, but keep what was being entered (the
          // note typed here is its only copy) so it can be checked and saved again
          return openLead(lead.id).then(function () {
            var latest = state.detail;
            if (latest && latest.id === lead.id) {
              var box = $('#yw-status-note');
              if (note && !box.value) box.value = note;
              $('#yw-set-status').value = status;
            }
            notice('Someone else changed this lead a moment ago (it is now ' +
                   (latest ? STATUSES[latest.status] || latest.status : 'changed') + '). Nothing was saved; ' +
                   'your choice' + (note ? ' and note are' : ' is') + ' still here. Check the history, then save again.',
                   true);
            loadLeads(false);
          });
        }
        failed(err);
      })
      .then(function () { state.busy = false; btn.disabled = false; });
  }

  function exportCsv() {
    var btn = $('[data-export]');
    btn.disabled = true;
    var headers = { 'Authorization': 'Bearer ' + (state.session && state.session.token),
                    'Content-Type': 'application/json', 'Accept': 'text/csv' };
    fetch('/admin/api/export', { method: 'POST', headers: headers, cache: 'no-store', credentials: 'omit',
                                 body: JSON.stringify(filters()) })
      .then(function (r) {
        if (r.status === 401) { sessionEnded(); throw { handled: true }; }
        if (!r.ok) {
          return r.json().catch(function () { return {}; }).then(function (data) {
            throw { error: (data && data.error) || 'The export did not work. Try again.' };
          });
        }
        var rows = parseInt(r.headers.get('X-Export-Rows') || '0', 10);
        var matching = parseInt(r.headers.get('X-Export-Matching') || '0', 10);
        if (matching > rows) {
          notice('The file holds the newest ' + rows + ' of ' + matching + ' matching leads. ' +
                 'Pick a status, a form or a search to export the others.', true);
        }
        return r.blob();
      })
      .then(function (blob) {
        var a = document.createElement('a');
        var url = URL.createObjectURL(blob);
        a.href = url;
        a.download = 'yantrai-leads-' + new Date().toISOString().slice(0, 10) + '.csv';
        document.body.appendChild(a);
        a.click();
        setTimeout(function () { URL.revokeObjectURL(url); a.remove(); }, 1000);
      })
      .catch(failed)
      .then(function () { btn.disabled = false; });
  }

  // ---- wiring ----
  function start(session) {
    saveSession(session);
    state.user = session.user || '';
    $('[data-gate]').hidden = true;
    $('[data-inbox]').hidden = false;
    $('[data-export]').hidden = false;
    $('[data-signout]').hidden = false;
    var u = $('[data-user]');
    u.textContent = state.user ? 'Signed in as ' + state.user : '';
    u.hidden = !state.user;
    loadLeads(false);
  }

  var searchTimer = null;
  $('[data-filters]').addEventListener('submit', function (e) { e.preventDefault(); });
  $('#yw-q').addEventListener('input', function (e) {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () { state.filters.q = e.target.value.trim(); loadLeads(false); }, 300);
  });
  $('#yw-status').addEventListener('change', function (e) { state.filters.status = e.target.value; loadLeads(false); });
  $('#yw-form').addEventListener('change', function (e) { state.filters.form = e.target.value; loadLeads(false); });
  $('[data-more]').addEventListener('click', function () { loadLeads(true); });
  $('[data-triage]').addEventListener('submit', saveStatus);
  $('[data-export]').addEventListener('click', exportCsv);
  $('[data-back]').addEventListener('click', function () {
    $('.panes').classList.remove('is-reading');
    state.selected = null;
  });
  $('[data-signout]').addEventListener('click', function () {
    saveSession(null);
    gate('You have signed out of YantrAI Web', 'Open it again from the platform whenever you need it.', true);
  });
  var home = $('[data-home]');
  if (platform) home.href = platform + '/'; else home.hidden = true;

  if (platformToken) {
    saveSession(null);
    api('POST', '/admin/api/session', { token: platformToken })
      .then(function (s) { start({ token: s.token, expires_at: s.expires_at, user: s.user }); })
      .catch(function (err) {
        if (err && err.handled) return;
        if (err && err.status === 403) {
          gate('YantrAI Web is for platform admins',
               'Your account can use the platform, but not this inbox. Ask a platform admin if you need access.', false);
        } else {
          gate('Could not open the inbox', (err && err.error) || 'Please try again from the platform.', true);
        }
      });
  } else {
    var existing = loadSession();
    if (existing) start(existing);
    else gate('Open YantrAI Web from the platform',
              'This inbox opens from its tile on the YantrAI platform, where you are signed in.', true);
  }
})();
