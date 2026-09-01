/* AiFA landing page — runtime behaviour.
 * Ported from the Claude Design canvas component (AiFA Hero White.dc.html)
 * so the page ships as static HTML with no framework on the wire. */
(function () {
  'use strict';

  /* Where the savings-check form posts. The site is static (GitHub Pages), so
     this must be an external form endpoint that accepts a cross-origin POST —
     Formspree / Basin / Getform, or your own API with CORS enabled.
     Until it is set, the form refuses to submit and tells the visitor to email
     instead, rather than silently swallowing a lead. */
  var FORM_ENDPOINT = '';
  var CAREER_ENDPOINT = '/api/careers';
  var FORM_FALLBACK_EMAIL = 'rohit@yantrailabs.com';
  var MAX_CV_BYTES = 10 * 1024 * 1024;

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var clamp = function (v, lo, hi) { return Math.max(lo, Math.min(hi, v)); };

  /* ---------------------------------------------------------------- data */

  var STAGES = [
    { name: 'Invoice entry', note: 'Money-out, tax, 3-way match and DOA agents make each entry right and compliant.',
      kind: 'The task', ref: 'Vendor invoice', stamp: 'Entered clean', dot: '#E69393',
      agents: [
        ['Duplicate', 'No earlier copy of this invoice'],
        ['Pricing', 'Price matches the contract and the PO'],
        ['3-way match', 'PO, GRN and invoice agree'],
        ['GST', 'Vendor has filed before credit is claimed'],
        ['TDS', 'Right section, right rate'],
        ['PAN', 'Vendor PAN active and linked'],
        ['MSME', 'Small-vendor clock started'],
        ['DOA', 'Inside the approval limit']
      ] },
    { name: 'Sync to ERP', note: 'Posted compliant to your ERP, so the book stays clean without a cleanup pass.',
      kind: 'The task', ref: 'Journal entry', stamp: 'Book clean', dot: '#98A5EF',
      agents: [
        ['Journal', 'Entry balanced, support attached'],
        ['Reconciliation', 'Sub-ledger agrees to the control account'],
        ['Intercompany', 'Counterparty entity agreed'],
        ['Period', 'Posted in the open period'],
        ['Audit', 'Immutable trail written'],
        ['Report', 'Books ready to report, no cleanup pass']
      ] },
    { name: 'Payment processing', note: 'The run is built from approved entries only, and checked again before it leaves.',
      kind: 'The task', ref: 'Payment run', stamp: 'Released', dot: '#5ADEB7',
      agents: [
        ['Payments', 'Run built from approved entries only'],
        ['Bank', 'Beneficiary account unchanged'],
        ['Duplicate', 'No second payment against the same invoice'],
        ['DOA', 'Released by two signatories'],
        ['Discount', 'Early-payment discount captured'],
        ['MSME', 'Paid inside the statutory window']
      ] }
  ];

  var GATES = [
    [0.06, '#B5C4F5', '#98A5EF'],
    [0.30, '#F9EBA6', '#EADC8F'],
    [0.54, '#F2A9A9', '#E69393'],
    [0.78, '#B2EEDC', '#5ADEB7']
  ];

  /* --------------------------------------------------------------- colour */

  function mix(a, b, t) {
    var p = function (s) {
      return [parseInt(s.slice(1, 3), 16), parseInt(s.slice(3, 5), 16), parseInt(s.slice(5, 7), 16)];
    };
    var c1 = p(a), c2 = p(b);
    var c = function (x, y) { return Math.round(x + (y - x) * t); };
    return 'rgb(' + c(c1[0], c2[0]) + ', ' + c(c1[1], c2[1]) + ', ' + c(c1[2], c2[2]) + ')';
  }

  // white -> yellow at half the checks -> green at all of them
  function scale(t) {
    var stops = [[0, '#FCF3D0', '#E8DFC0'], [0.5, '#F9EBA6', '#EADC8F'], [1, '#B2EEDC', '#5ADEB7']];
    var q = clamp(t, 0, 1);
    for (var i = 1; i < stops.length; i++) {
      if (q <= stops[i][0]) {
        var a = stops[i - 1], b = stops[i];
        var k = (q - a[0]) / (b[0] - a[0]);
        return { fill: mix(a[1], b[1], k), edge: mix(a[2], b[2], k) };
      }
    }
    return { fill: stops[2][1], edge: stops[2][2] };
  }

  /* ------------------------------------------------------------- elements */

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  var heroEl      = $('[data-hero]');
  var agentsEl    = document.getElementById('agents');
  var proofEl     = document.getElementById('proof');
  var ringEl      = $('[data-ring]');
  var arcEl       = $('[data-ring-arc]');
  var adherenceEl = $('[data-adherence]');
  var taskKindEl  = $('[data-task-kind]');
  var taskRefEl   = $('[data-task-ref]');
  var stampEl     = $('[data-stamp]');
  var stageEls    = $$('[data-stage]');
  var ringAgentEls = $$('[data-ring-agent]');
  var policyEls   = $$('[data-policy]');

  var gateEls = [1, 2, 3, 4].map(function (n) {
    return {
      card:    $('[data-gate-card="' + n + '"]'),
      conn:    $('[data-gate-conn="' + n + '"]'),
      connDot: $('[data-gate-conn-dot="' + n + '"]'),
      glyph:   $('[data-gate-glyph="' + n + '"]'),
      label:   $('[data-gate-label="' + n + '"]'),
      side:    $('[data-gate-side="' + n + '"]')
    };
  });

  var setText = function (el, v) { if (el && el.textContent !== v) el.textContent = v; };

  /* ---------------------------------------------------------- ring width */

  var lastAp = -1;
  var lastGp = -1;
  var ringW = 430;
  if (ringEl) {
    var measure = function () {
      var w = ringEl.getBoundingClientRect().width;
      if (w && Math.abs(w - ringW) > 1) { ringW = w; renderAgents(lastAp, true); }
    };
    measure();
    if (typeof ResizeObserver !== 'undefined') new ResizeObserver(measure).observe(ringEl);
  }

  /* ---------------------------------------------------------- progress */

  function progress(el) {
    if (!el) return 0;
    var r = el.getBoundingClientRect();
    var span = r.height - (window.innerHeight || 800);
    if (span <= 0) return r.top <= 0 ? 1 : 0;
    return clamp(-r.top / span, 0, 1);
  }

  /* ------------------------------------------------------- agents render */

  function renderAgents(ap, force) {
    if (!agentsEl) return;
    if (!isFinite(ap)) ap = 0;
    if (!force && ap === lastAp) return;
    lastAp = ap;

    var nS = STAGES.length;
    var si = clamp(Math.floor(ap * nS - 1e-6), 0, nS - 1);
    var st = STAGES[si];
    var within = clamp(ap * nS - si, 0, 1);
    var n = st.agents.length;
    var lit = clamp(Math.floor((within / 0.62) * n), 0, n);
    var done = lit >= n;
    var cont = clamp(within / 0.62, 0, 1);
    var sc = scale(cont);
    var R = Math.max(88, (ringW / 2) - 54);

    setText(adherenceEl, Math.round((lit / n) * 100) + '%');
    setText(taskKindEl, st.kind);
    setText(taskRefEl, st.ref);

    if (stampEl) {
      setText(stampEl, done ? st.stamp : 'In check');
      stampEl.style.background = done ? sc.fill : '#F5F7F3';
      stampEl.style.borderColor = done ? sc.edge : '#DCE0DA';
    }

    if (arcEl) {
      var arcDeg = (lit / n) * 360;
      arcEl.style.background =
        'conic-gradient(' + sc.edge + ' 0deg ' + arcDeg + 'deg, #ECEFEA ' + arcDeg + 'deg 360deg)';
    }

    stageEls.forEach(function (el, i) {
      var on = i === si;
      var s = STAGES[i];
      el.style.borderLeftColor = on ? s.dot : '#E6EAE4';
      el.style.background = on ? '#F5F7F3' : 'transparent';
      var nameEl = $('[data-stage-name]', el);
      if (nameEl) { setText(nameEl, s.name); nameEl.style.color = on ? '#151414' : '#767676'; }
      var noteEl = el.children[1];
      if (noteEl && noteEl !== nameEl) setText(noteEl, s.note);
    });

    ringAgentEls.forEach(function (el, i) {
      if (i >= n) { el.style.display = 'none'; return; }
      el.style.display = 'inline-flex';
      var th = -90 + i * (360 / n);
      var on = i < lit;
      el.style.transform =
        'translate(-50%, -50%) rotate(' + th + 'deg) translate(0, -' + R + 'px) rotate(' + (-th) + 'deg)';
      el.style.background = on ? sc.fill : '#FFFFFF';
      el.style.borderColor = on ? sc.edge : '#DCE0DA';
      el.style.color = on ? '#151414' : '#5A5A5A';
      el.style.opacity = on ? 1 : 0.88;
      var dot = $('[data-dot]', el);
      if (dot) dot.style.background = on ? sc.edge : '#DCE0DA';
      var nameEl = $('[data-ring-agent-name]', el);
      if (nameEl) setText(nameEl, st.agents[i][0] + ' Agent');
    });

    policyEls.forEach(function (el, i) {
      if (i >= n) { el.style.display = 'none'; return; }
      el.style.display = 'flex';
      var on = i < lit;
      el.style.opacity = on ? 1 : 0;
      el.style.transform = 'translateX(' + (on ? '0px' : '-14px') + ')';
      var dot = $('[data-pdot]', el);
      if (dot) dot.style.background = sc.edge;
      var textEl = el.children[1];
      if (textEl) setText(textEl, st.agents[i][1]);
    });
  }

  /* -------------------------------------------------------- gates render */

  function renderGates(gp) {
    if (!proofEl || !isFinite(gp) || gp === lastGp) return;
    lastGp = gp;

    GATES.forEach(function (g, i) {
      var on = i === 0 ? true : gp >= g[0];
      var bg = g[1], bd = g[2];
      var e = gateEls[i];
      if (!e) return;

      if (e.card) {
        e.card.style.zIndex = on ? 20 : 4 - i;
        e.card.style.background = on ? bg : '#F5F7F3';
        e.card.style.borderColor = on ? bd : '#DCE0DA';
        e.card.style.transform = on
          ? 'rotateX(14deg) translateZ(60px)'
          : 'rotateX(48deg) translateZ(' + (-40 * (GATES.length - i)) + 'px)';
        e.card.style.boxShadow = on
          ? '0 26px 44px rgba(21, 20, 20, 0.16)'
          : '0 10px 20px rgba(21, 20, 20, 0.06)';
      }
      if (e.conn) { e.conn.style.background = on ? bd : '#DCE0DA'; e.conn.style.opacity = on ? 1 : 0; }
      if (e.connDot) e.connDot.style.background = on ? bd : '#DCE0DA';
      if (e.glyph) e.glyph.style.opacity = on ? 1 : 0.3;
      if (e.label) e.label.style.color = on ? '#151414' : '#767676';
      if (e.side) {
        e.side.style.opacity = on ? 1 : 0;
        e.side.style.transform = 'translateX(' + (on ? '0px' : ((i + 1) % 2 === 0 ? '-26px' : '26px')) + ')';
      }
    });
  }

  /* ---------------------------------------------------------- scroll loop */

  var raf = null;

  function onScroll() {
    if (raf) return;
    raf = requestAnimationFrame(function () {
      raf = null;
      renderGates(Math.round(progress(proofEl) * 200) / 200);
      renderAgents(Math.round(progress(agentsEl) * 300) / 300);
      if (heroEl) {
        var vh = window.innerHeight || 800;
        var bottom = heroEl.getBoundingClientRect().bottom;
        heroEl.style.setProperty('--q', String(clamp((vh - bottom) / (vh * 0.55), 0, 1)));
      }
    });
  }

  window.addEventListener('scroll', onScroll, { passive: true });
  window.addEventListener('resize', onScroll, { passive: true });

  /* ---------------------------------------------------------- trust band */

  var trustEl = $('[data-trust]');
  if (trustEl) {
    var revealTrust = function () {
      trustEl.style.opacity = 1;
      trustEl.style.transform = 'none';
    };
    if (reduceMotion || typeof IntersectionObserver === 'undefined') {
      revealTrust();
    } else if (trustEl.getBoundingClientRect().top < (window.innerHeight || 0) * 0.92) {
      revealTrust();
    } else {
      trustEl.style.opacity = 0;
      trustEl.style.transform = 'translateY(20px)';
      var io = new IntersectionObserver(function (entries) {
        for (var i = 0; i < entries.length; i++) {
          if (entries[i].isIntersecting) { revealTrust(); io.disconnect(); return; }
        }
      }, { threshold: 0.12 });
      io.observe(trustEl);
      setTimeout(revealTrust, 2600);
    }
  }

  /* --------------------------------------------------------------- video */

  var videoEl = $('[data-video]');
  var videoWrap = $('[data-video-wrap]');
  var hintEl = $('[data-video-hint]');
  var soundBtn = $('[data-sound-btn]');
  var VIDEO_SRC = 'assets/aifa-agent-teams-60s.mp4';

  if (videoEl) {
    // Pointer devices play on hover (as designed). Touch devices have no hover,
    // so there the video plays whenever the section is on screen.
    var hoverCapable = window.matchMedia('(hover: hover)').matches;
    var inView = false, hovering = false, soundOn = false;

    videoEl.muted = true;

    var sync = function () {
      var should = inView && (hoverCapable ? hovering : true);
      if (should) {
        if (!videoEl.getAttribute('src')) videoEl.setAttribute('src', VIDEO_SRC);
        var p = videoEl.play();
        if (p && p.catch) p.catch(function () {});
      } else if (!videoEl.paused) {
        videoEl.pause();
      }
      if (hintEl) hintEl.style.opacity = should ? 0 : 1;
    };

    if (typeof IntersectionObserver !== 'undefined') {
      new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          inView = e.isIntersecting;
          if (inView && !videoEl.getAttribute('src')) videoEl.setAttribute('src', VIDEO_SRC);
          sync();
        });
      }, { threshold: 0.3 }).observe(videoEl);
    } else {
      videoEl.setAttribute('src', VIDEO_SRC);
    }

    if (videoWrap && hoverCapable) {
      videoWrap.addEventListener('mouseenter', function () { hovering = true; sync(); });
      videoWrap.addEventListener('mouseleave', function () { hovering = false; sync(); });
    }
    if (!hoverCapable && hintEl) hintEl.style.display = 'none';

    if (soundBtn) {
      soundBtn.addEventListener('click', function () {
        soundOn = !soundOn;
        videoEl.muted = !soundOn;
        soundBtn.textContent = soundOn ? 'Sound on' : 'Sound off';
        soundBtn.setAttribute('aria-pressed', String(soundOn));
        if (soundOn) {
          if (!videoEl.getAttribute('src')) videoEl.setAttribute('src', VIDEO_SRC);
          var p = videoEl.play();
          if (p && p.catch) p.catch(function () {});
        }
      });
    }
  }

  /* ---------------------------------------------------------- form wiring */

  var form     = $('[data-book-form]');
  var teaser   = $('[data-book-teaser]');
  var openBtn  = $('[data-book-open]');

  // The form stays closed until someone asks for it — on this button, or on any
  // CTA pointing at #book (those all say some version of "check your savings",
  // so landing there with the fields already open is what the click meant).
  function openForm(focus) {
    if (!form || !form.hidden) return;
    form.hidden = false;
    if (teaser) teaser.hidden = true;
    if (openBtn) openBtn.setAttribute('aria-expanded', 'true');
    if (focus) {
      var first = document.getElementById('f-name');
      if (first) first.focus({ preventScroll: true });
    }
  }

  if (openBtn) openBtn.addEventListener('click', function () { openForm(true); });

  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[href="#book"]');
    if (a) openForm(false);
  });

  var doneEl   = $('[data-book-done]');
  var errorEl  = $('[data-book-error]');
  var submitEl = $('[data-book-submit]');
  var labelEl  = $('[data-submit-label]');

  function showError(msg) {
    if (!errorEl) return;
    errorEl.textContent = msg;
    errorEl.hidden = false;
  }

  function validate() {
    var ok = true, firstBad = null;
    ['f-name', 'f-email', 'f-company'].forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      var bad = !el.value.trim() || (el.type === 'email' && !/^[^@\s]+@[^@\s.]+\.[^@\s]+$/.test(el.value.trim()));
      el.setAttribute('aria-invalid', bad ? 'true' : 'false');
      if (bad && !firstBad) firstBad = el;
      if (bad) ok = false;
    });
    if (firstBad) firstBad.focus();
    return ok;
  }

  if (form) {
    form.addEventListener('input', function (e) {
      if (e.target.getAttribute('aria-invalid') === 'true') e.target.setAttribute('aria-invalid', 'false');
      if (errorEl) errorEl.hidden = true;
    });

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      if (errorEl) errorEl.hidden = true;

      // honeypot: a bot filled a field no person can see
      var trap = document.getElementById('f-website');
      if (trap && trap.value) return;

      if (!validate()) {
        showError('Please fill in your name, work email and company.');
        return;
      }

      if (!FORM_ENDPOINT) {
        showError('The form is not connected yet — please email ' + FORM_FALLBACK_EMAIL + '.');
        return;
      }

      var data = {};
      new FormData(form).forEach(function (v, k) { if (k !== 'website') data[k] = v; });
      data.page = location.href;

      submitEl.disabled = true;
      if (labelEl) labelEl.textContent = 'Sending';

      fetch(FORM_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
        body: JSON.stringify(data)
      }).then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        form.hidden = true;
        if (doneEl) doneEl.hidden = false;
      }).catch(function () {
        submitEl.disabled = false;
        if (labelEl) labelEl.textContent = 'Check your savings';
        showError('That did not go through. Please try again, or email ' + FORM_FALLBACK_EMAIL + '.');
      });
    });
  }

  /* ------------------------------------------------------------- careers */

  var cForm   = $('[data-career-form]');
  var cDone   = $('[data-career-done]');
  var cError  = $('[data-career-error]');
  var cSubmit = $('[data-career-submit]');
  var cFile   = document.getElementById('c-resume');
  var cFileName = $('[data-file-name]');

  if (cFile && cFileName) {
    cFile.addEventListener('change', function () {
      var f = cFile.files && cFile.files[0];
      cFileName.hidden = !f;
      if (f) cFileName.textContent = f.name + ' · ' + Math.round(f.size / 1024) + ' KB';
    });
  }

  if (cForm) {
    function cShowError(msg) {
      if (!cError) return;
      cError.textContent = msg;
      cError.hidden = false;
    }

    cForm.addEventListener('input', function () { if (cError) cError.hidden = true; });

    cForm.addEventListener('submit', function (e) {
      e.preventDefault();
      if (cError) cError.hidden = true;

      var trap = document.getElementById('c-website');
      if (trap && trap.value) return;

      var ok = true, firstBad = null;
      ['c-name', 'c-email'].forEach(function (id) {
        var el = document.getElementById(id);
        if (!el) return;
        var bad = !el.value.trim() ||
          (el.type === 'email' && !/^[^@\s]+@[^@\s.]+\.[^@\s]+$/.test(el.value.trim()));
        el.setAttribute('aria-invalid', bad ? 'true' : 'false');
        if (bad && !firstBad) firstBad = el;
        if (bad) ok = false;
      });
      if (!ok) {
        if (firstBad) firstBad.focus();
        cShowError('Please give us a name and an email we can reply to.');
        return;
      }

      var f = cFile && cFile.files && cFile.files[0];
      if (f && f.size > MAX_CV_BYTES) {
        cShowError('That CV is over 10 MB. Send a smaller file, or email it to ' +
                   FORM_FALLBACK_EMAIL + '.');
        return;
      }

      var body = new FormData(cForm);
      body.delete('website');
      body.append('page', location.href);

      cSubmit.disabled = true;
      var cLabel = $('[data-submit-label]', cForm);
      if (cLabel) cLabel.textContent = 'Sending';

      fetch(CAREER_ENDPOINT, { method: 'POST', body: body })
        .then(function (r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          cForm.hidden = true;
          if (cDone) cDone.hidden = false;
        })
        .catch(function () {
          cSubmit.disabled = false;
          if (cLabel) cLabel.textContent = 'Send it to a founder';
          cShowError('That did not go through. Please try again, or email ' +
                     FORM_FALLBACK_EMAIL + '.');
        });
    });
  }

  /* --------------------------------------------------- CTA / footer routing */

  // Everything the artboard left as href="#" that has an obvious on-page home.
  var ROUTES = {
    // real pages
    'duplicate': '/agents/duplicate-agent', 'pricing': '/agents/pricing-agent',
    '3-way match': '/agents/3-way-match-agent', 'gst': '/agents/gst-agent',
    'tds': '/agents/tds-agent', 'discount': '/agents/discount-agent',
    'view all agents': '/agents',
    'invoice entry': '/workflows/invoice-entry', 'sync to erp': '/workflows/sync-to-erp',
    'payment processing': '/workflows/payment-processing',
    'what it found': '/what-it-found', 'security': '/security',
    // still on-page anchors
    'how aifa works': '/#how', 'implementation': '/#integration',
    'support': '/#book', 'contact': '/#book',
    'anything with an export': '/#integration',
    'payments': '/agents', 'msme': '/agents',
    'daily close': '/workflows/sync-to-erp',
    'multi-entity groups': '/workflows/sync-to-erp',
    'multi-currency': '/workflows/sync-to-erp',
    'sap': '/#integration', 'oracle': '/#integration', 'oracle netsuite': '/#integration',
    'tally': '/#integration', 'zoho books': '/#integration', 'quickbooks': '/#integration',
    'sage': '/#integration', 'odoo': '/#integration',
    'cfo': '/#book', 'controller': '/#book', 'head of finance': '/#book',
    'ap lead': '/#book', 'group treasurer': '/#book', 'internal audit': '/#book'
  };


  $$('a[data-placeholder-link]').forEach(function (a) {
    var key = a.textContent.trim().toLowerCase()
      .replace(/\s*\u2192$/, '')          // trailing arrow
      .replace(/\s+agent$/, '')            // "Duplicate Agent" -> "duplicate"
      .trim();
    var target = ROUTES[key];
    if (target) {
      a.setAttribute('href', target);
      a.removeAttribute('data-placeholder-link');
    }
  });

  // Anything still unrouted stays inert rather than jumping to the top.
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[data-placeholder-link]');
    if (a) e.preventDefault();
  });

  /* -------------------------------------------------------------- locale */

  // The nav is shared across every page, so its switcher links point at the
  // site root. Retarget them at the page actually being read, and record the
  // choice — the server stops guessing from Accept-Language once it is set.
  var LOCALES = ['en', 'fr'];

  function pathLocale(p) {
    return (p === '/fr' || p.indexOf('/fr/') === 0) ? 'fr' : 'en';
  }

  function pathIn(p, locale) {
    var rest = pathLocale(p) === 'fr' ? p.slice(3) : p;
    if (rest.charAt(0) !== '/') rest = '/' + rest;
    return locale === 'en' ? rest : '/fr' + rest;
  }

  $$('[data-lang-switch] a[data-set-lang]').forEach(function (a) {
    var code = a.getAttribute('data-set-lang');
    if (LOCALES.indexOf(code) === -1) return;
    a.setAttribute('href', pathIn(location.pathname, code) + location.hash);
    a.addEventListener('click', function () {
      document.cookie = 'lang=' + code + ';path=/;max-age=31536000;samesite=lax';
    });
  });

  /* ----------------------------------------------------------- mobile nav */

  var navToggle = $('[data-nav-toggle]');
  var navLinks = $('[data-nav-links]');
  if (navToggle && navLinks) {
    navToggle.addEventListener('click', function () {
      var open = document.documentElement.classList.toggle('nav-open');
      navToggle.setAttribute('aria-expanded', String(open));
    });
    navLinks.addEventListener('click', function (e) {
      if (e.target.tagName === 'A') {
        document.documentElement.classList.remove('nav-open');
        navToggle.setAttribute('aria-expanded', 'false');
      }
    });
  }

  /* ------------------------------------------------------------- kick off */

  renderAgents(0, true);
  renderGates(0);
  onScroll();
})();
