"""Builds the whole Ohm draft site into ../site/ (complete pages) and
home_v11.html (artifact index, no doctype). One shell, plain labels, content
only from approved sources. See ../05_site_map.md."""
import base64,os,re,mimetypes
HERE=os.path.dirname(os.path.abspath(__file__)); os.chdir(HERE)
# OHM_LIVE=1 builds the pages yantrailabs.com serves: folder addresses, asset
# files instead of inline images, full <head>, written into <repo>/public.
# Without it the same pages are built as a flat draft into ../site.
LIVE=os.environ.get('OHM_LIVE')=='1'
REPO=os.path.normpath(os.path.join(HERE,'..','..'))
OUT=os.path.join(REPO,'public') if LIVE else os.path.normpath(os.path.join(HERE,'..','site')); os.makedirs(OUT,exist_ok=True)
SITE='https://yantrailabs.com'
# draft file -> live address. Company keeps the live /about address.
ROUTE={'index.html':'/','accountant.html':'/accountant','auditor.html':'/auditor','command.html':'/command','pricing.html':'/pricing','customers.html':'/customers','security.html':'/security','partners.html':'/partners','company.html':'/about','privacy.html':'/privacy','terms.html':'/terms'}
# Privacy and Terms on the live site are the approved pages from design/; the short drafts here are not published.
LIVE_SKIP={'privacy.html','terms.html'}
VIDEO={'video/ohm_hi_16x9.mp4':'/assets/ohm-invite.mp4','video/ohm_hi_9x16.mp4':'/assets/ohm-invite-9x16.mp4','video/ohm_en_16x9.mp4':'/assets/ohm-invite-en.mp4','video/ohm_en_9x16.mp4':'/assets/ohm-invite-en-9x16.mp4'}
# title and description for the live <head>; descriptions reuse lines already on the page
META={
 'index.html':('OHM — The AI agents for finance | YantrAI','Hire your AI Accountant and AI Auditor. From bill entry and writing in Tally to generating a MIS.'),
 'accountant.html':('AI Accountant | OHM','From bill entry and writing in Tally to generating a MIS. Invoice processing, bank reconciliation, and it talks to Tally.'),
 'auditor.html':('AI Auditor | OHM','Checks every voucher before money moves. Reconciles the books. Closes and certifies the month.'),
 'command.html':('Get anything done with a command | OHM','Just ask, in plain words. On your phone or on the web.'),
 'pricing.html':('Pricing | OHM','Intern free for 20 new transactions a month; Executive ₹2,999 for 1,000; Manager ₹4,999 for 3,000. Plus GST.'),
 'customers.html':('Customers | OHM','Companies we work with, in India, Dubai, the USA and Africa.'),
 'security.html':('Security | OHM','Your data remains private. No LLM is ever trained on it. We run our own model.'),
 'partners.html':('Partners | OHM','Partner-first network. Customer for lifetime. Earn 3× of Tally.'),
 'company.html':('About YantrAI Labs | OHM','Ohm is built by YantrAI Labs. Vision, mission, team, careers and contact.'),
}
# old addresses on the previous home page that other live pages still link to
LEGACYJS='''
(function(){ var m={book:'/about#contact',proof:'/customers',research:'/research',how:'#film',agents:'#products',integration:'#works',problem:'#problems'};
  function go(){ var t=m[location.hash.slice(1)]; if(!t) return;
    if(t.charAt(0)==='#'){ var el=document.getElementById(t.slice(1)); if(el) el.scrollIntoView(); } else location.replace(t); }
  go(); window.addEventListener('hashchange',go); })();
'''
SIGNUP='https://workspace.yantrailabs.com/signup'; LOGIN='https://workspace.yantrailabs.com/'
ARR='<svg><use href="#arr"/></svg>'
def rd(f): return open(f,encoding='utf-8').read()
def parts(f):
    b=rd(f); return b.split('<!--CSS-->')[1].split('<!--HTML-->')[0], b.split('<!--HTML-->')[1]
tpl=rd('hero_v11_template.html')
c1,h1=parts('blocks_v1.html'); c2,h2=parts('blocks_v2.html')
pb=rd('partner_blocks.html'); pc=pb.split('<!--CSS-->')[1].split('<!--HTML-->')[0]; ph=pb.split('<!--HTML-->')[1].split('<!--JS-->')[0]; pj=pb.split('<!--JS-->')[1]
pricing=rd('blocks_pricing.html')

def ext(href): return href.startswith('http')
def a(cls,href,inner,extra=''):
    t=' target="_blank" rel="noopener"' if ext(href) else ''
    c=f' class="{cls}"' if cls else ''
    return f'<a{c} href="{href}"{t}{extra}>{inner}</a>'
def sub(s,old,new,n=None):
    k=s.count(old); assert k>0 and (n is None or k==n),(old[:60],k)
    return s.replace(old,new)

# ---------- pieces of the hero template ----------
HEADLINKS='\n'.join(tpl.splitlines()[1:3])
TPLCSS=tpl[tpl.index('<style>')+7:tpl.index('</style>')]
SYMBOLS=tpl[tpl.index('<svg width="0"'):tpl.index('</svg>',tpl.index('<svg width="0"'))+6]
marks=['<!-- 1 Accountant -->','<!-- 2 Auditor -->','<!-- 3 Command -->','<!-- 4 Private -->']
endm='    </div>\n    <div class="in tabsbar"'
pos=[tpl.index(m) for m in marks]+[tpl.index(endm)]
START_BTN='<span class="btn p">Start free '+ARR+'</span>'
START_A=a('btn p',SIGNUP,'Start free '+ARR)
SLIDES=[tpl[pos[i]+len(marks[i]):pos[i+1]].strip().replace(' hidden>','>').replace(START_BTN,START_A) for i in range(4)]
HOMEBODY=tpl[tpl.index('  <section class="band" aria-roledescription="carousel"'):tpl.index('\n</div>\n\n<div class="in note">')]
script=tpl[tpl.rindex('<script>')+8:tpl.rindex('</script>')]
script='\n'.join(l for l in script.splitlines() if "getElementById('menubtn')" not in l and '/* phone menu */' not in l)
CONVOS=script[script.index('  var convos=['):script.index("  var cq=document.getElementById('cqt')")]
CHATFN=script[script.index("  var cq=document.getElementById('cqt')"):script.index("  var tr=document.getElementById('track')")]

# ---------- shared css ----------
SITECSS='''
/* ===== site shell ===== */
[hidden]{display:none!important}
body{background:var(--bg);color:var(--ink)}
.site{border:0}
.site a{color:inherit;text-decoration:none}
.site a.btn.p{color:#fff} .cout a.btn{color:var(--brand)}
.strip .tabs a{display:inline-flex;align-items:center;height:100%;border-bottom:2px solid transparent}
.strip .tabs a.on{color:var(--brand);border-bottom-color:var(--brand);font-weight:500}
.strip .tabs a:hover,.nav .links a:hover,.nav .right>a:not(.btn):hover,.foot .r1 a:hover,.ddb:hover{color:var(--brand)}
.nav .links{height:100%;align-items:center}
.nav .links a{display:inline-flex;align-items:center;gap:6px}
.nav .links a.on{color:var(--brand)}
.dd{position:relative;display:inline-flex;align-items:center;height:100%}
.ddb{background:none;border:0;padding:0;font:inherit;color:inherit;display:inline-flex;align-items:center;gap:6px;cursor:pointer;height:100%}
.ddp{display:none;position:absolute;top:100%;left:-14px;min-width:220px;background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);padding:8px;z-index:30;font-weight:400;font-size:14.5px}
.ddp a{display:block;padding:10px 12px;border-radius:8px;white-space:nowrap}
.ddp a:hover{background:var(--band);color:var(--brand)}
.dd.open .ddp{display:block}
@media (hover:hover){ .dd:hover .ddp{display:block} }
button.ico{background:none;border:0;padding:0;color:inherit;cursor:pointer}
button.ico:hover{color:var(--brand)}
.searchp{border-bottom:1px solid var(--line);background:var(--surface)}
.searchp .in{padding-block:16px 18px;display:grid;gap:12px}
.searchp input{font:inherit;font-size:16px;padding:12px 14px;border:1px solid #C9CCE0;border-radius:9px;width:100%;max-width:560px;color:var(--ink);background:var(--surface)}
.searchp input:focus{outline:2px solid var(--brand);outline-offset:1px}
.searchp .res{display:flex;flex-wrap:wrap;gap:8px}
.searchp .res a{border:1px solid var(--line);border-radius:999px;padding:7px 14px;font-size:14px}
.searchp .res a:hover{border-color:var(--brand);color:var(--brand)}
.sheet .grp a{min-height:44px;display:flex;align-items:center;border-bottom:1px solid var(--line)}
.sheet .grp a.btn{border-bottom:0;justify-content:center}
.logos .lead a:hover b{color:var(--brand)}
@media (max-width:980px){ .strip .dd{display:none} }
/* ===== page blocks ===== */
.pgtop{padding-block:56px 36px}
.pgtop .lead{margin-top:12px}
.pg{padding-block:60px 56px;border-top:1px solid var(--line)}
.pg>.h2+*{margin-top:28px}
.pg>.h2+.lead{margin-top:10px}
.pg .lead+*{margin-top:28px}
.boxes{display:grid;gap:16px;grid-template-columns:repeat(2,minmax(0,1fr))}
.boxes.c3{grid-template-columns:repeat(3,minmax(0,1fr))}
.boxes.c4{grid-template-columns:repeat(4,minmax(0,1fr))}
.pbox .you{margin-top:14px;padding-top:14px;border-top:1px solid var(--line);display:flex;gap:12px;font-size:14.5px;align-items:baseline;line-height:1.45}
.pbox .you b{font-size:11.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);font-weight:600;flex:none}
.pbox h3 a::after{content:"";position:absolute;inset:0;border-radius:14px}
.pbox.lk{transition:border-color .15s} .pbox.lk:hover{border-color:var(--brand)} .pbox.lk:hover h3{color:var(--brand)}
.pbox .role{font-size:13.5px;color:var(--muted);margin-top:-8px;padding-bottom:14px;border-bottom:1px solid var(--line)}
.pbox.plain h3{border-bottom:0;padding-bottom:6px;margin-right:0}
.pbox.plain p{font-size:15px;line-height:1.5;color:var(--muted)}
.pbox.plain p.bigl{font-family:var(--sans);font-weight:500;font-size:clamp(24px,2.6vw,32px);letter-spacing:-.02em;line-height:1.12;color:var(--ink)}
.pbox .lbl{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);font-weight:600;margin-bottom:12px}
.flow{display:flex;flex-wrap:wrap;align-items:center;gap:12px 14px}
.flow .st{display:flex;align-items:center;gap:12px;border:1px solid var(--line);border-radius:12px;padding:14px 18px;background:var(--surface);font-family:var(--sans);font-weight:500;font-size:17px;letter-spacing:-.01em}
.flow .st i{font-style:normal;width:26px;height:26px;border-radius:50%;background:var(--tint);color:var(--brand);display:grid;place-items:center;font-size:13px;font-weight:600;flex:none}
.flow .st.on{background:var(--brand);color:#fff;border-color:var(--brand)} .flow .st.on i{background:#fff}
.flow .ar{width:18px;height:18px;color:var(--muted);flex:none}
.flow .ar svg{width:100%;height:100%;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.stats div{border:1px solid var(--line);border-radius:14px;padding:22px;background:var(--surface)}
.stats b{display:block;font-family:var(--sans);font-weight:500;font-size:clamp(28px,3vw,40px);letter-spacing:-.025em;line-height:1}
.stats span{display:block;color:var(--muted);font-size:14px;margin-top:8px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}
.chips span{border:1px solid var(--line);border-radius:999px;padding:6px 14px;font-size:14px;background:var(--surface)}
.sig{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}
@media (min-width:900px) and (max-width:1179px){ .sig{grid-template-columns:repeat(2,minmax(0,1fr))} }
.sig>div{border:1px solid var(--line);border-radius:14px;padding:20px 22px;display:grid;grid-template-columns:auto 1fr;gap:4px 14px;align-items:center;background:var(--surface)}
.sig i{grid-row:1/3;width:44px;height:44px;border-radius:12px;background:var(--tint);color:var(--brand);display:grid;place-items:center}
.sig i svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
.sig b{font-family:var(--sans);font-weight:500;font-size:17px;letter-spacing:-.01em;align-self:end}
.sig span{color:var(--muted);font-size:14px;align-self:start}
.lgrid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}
.lgrid div{border:1px solid var(--line);border-radius:14px;padding:30px 18px 18px;display:grid;gap:18px;justify-items:center;align-content:space-between;min-height:160px;background:var(--surface)}
.lgrid img{height:44px;width:auto;max-width:78%;object-fit:contain;opacity:.85}
.lgrid span{font-size:14px;color:var(--muted);text-align:center}
.pgcta{padding-block:44px 48px;border-top:1px solid var(--line);background:var(--band)}
.pgcta .in{display:flex;gap:14px 16px;align-items:center;flex-wrap:wrap}
.contact2 .pbox p{font-size:16px;line-height:1.7}
.sel{user-select:all}
.legal>*{max-width:760px}
.pp .in{max-width:1280px}
.legal .draft{display:inline-block;background:#FBEBD9;color:#9A5312;font-weight:600;font-size:12px;border-radius:999px;padding:4px 12px;margin-top:14px}
.legal h3{font-family:var(--sans);font-weight:500;font-size:20px;letter-spacing:-.015em;margin:30px 0 8px}
.legal p,.legal li{font-size:15.5px;line-height:1.6;color:var(--ink)}
.legal ul{margin:6px 0 0;padding-left:20px} .legal li{margin:4px 0}
.price{border-top:0}
@media (max-width:1100px){ .boxes.c4{grid-template-columns:repeat(2,minmax(0,1fr))} }
@media (max-width:899px){ .boxes,.boxes.c3,.boxes.c4,.sig,.stats{grid-template-columns:1fr} .lgrid{grid-template-columns:repeat(2,minmax(0,1fr))} }
@media (max-width:640px){ .pg{padding-block:40px 36px} .pgtop{padding-block:36px 24px} .flow{display:grid;justify-items:start} .flow .ar{transform:rotate(90deg);margin-left:24px} .lgrid div{min-height:130px;padding:22px 12px 14px} .lgrid img{height:34px} }
'''
CSS=TPLCSS+c1+c2+pc+SITECSS

# ---------- shell ----------
CARET='<svg class="caret"><use href="#caret"/></svg>'
COMPANY=[('About','company.html'),('Team','company.html#team'),('Careers','company.html#careers'),('Contact','company.html#contact')]
PRODUCTS=[('Ohm Accountant','accountant.html'),('Ohm Auditor','auditor.html'),('Get anything done','command.html')]
def header(active):
    on=lambda k:' class="on"' if active==k else ''
    dd=lambda label,items:f'<div class="dd"><button class="ddb" type="button" aria-expanded="false" aria-haspopup="true">{label} {CARET}</button><div class="ddp">'+''.join(a('',u,t) for t,u in items)+'</div></div>'
    return f'''<div class="site">
  <div class="strip"><div class="in">
    <a class="wm" href="index.html" aria-label="Ohm home"><svg><use href="#mark"/></svg>OHM</a>
    {dd('Company',COMPANY)}
    <span class="tabs"><a{on('accountant')} href="accountant.html">Ohm Accountant</a><a{on('auditor')} href="auditor.html">Ohm Auditor</a><a{on('partners')} href="partners.html">Partners</a></span>
    <button class="menubtn" id="menubtn" type="button" aria-expanded="false" aria-controls="sheet">Menu</button>
  </div></div>
  <div class="sheet" id="sheet" hidden>
    <div class="in">
      <div class="grp"><b>Products</b>{''.join(a('',u,t) for t,u in PRODUCTS)}</div>
      <div class="grp">{a('','pricing.html','Pricing')}{a('','customers.html','Customers')}{a('','security.html','Security')}{a('','partners.html','Partners')}</div>
      <div class="grp"><b>Company</b>{''.join(a('',u,t) for t,u in COMPANY)}</div>
      <div class="grp">{a('',LOGIN,'Log in')}{a('btn p sm','partners.html','Become a partner '+ARR)}</div>
    </div>
  </div>
  <nav class="nav" aria-label="Main"><div class="in">
    <span class="links">{dd('Products',PRODUCTS)}<a{on('pricing')} href="pricing.html">Pricing</a><a{on('customers')} href="customers.html">Customers</a><a{on('security')} href="security.html">Security</a></span>
    <span class="mid">by YantrAI Labs</span>
    <span class="right">
      <button class="ico" id="searchbtn" type="button" aria-label="Search" aria-expanded="false" aria-controls="searchp"><svg viewBox="0 0 20 20"><circle cx="9" cy="9" r="6"/><path d="M14 14l4 4"/></svg></button>
      <span class="sep"></span>{a('',LOGIN,'Log in')}
      {a('btn p sm','partners.html','Become a partner '+ARR)}
    </span>
  </div></nav>
  <div class="searchp" id="searchp" hidden><div class="in"><input type="search" placeholder="Search" aria-label="Search the site"><div class="res"></div></div></div>
'''
# Live pages carry the slot the running service fills with its commit and revision (OPS-08), as every other page on the site does.
COPY='<span>© 2026 YantrAI Labs<span data-site-version="1" hidden></span></span>' if LIVE else '© 2026 YantrAI Labs'
VERSIONJS='''
(function(){ var el=document.querySelector('[data-site-version]'); if(!el||!window.fetch) return;
  fetch('/_status',{cache:'no-store'}).then(function(r){ return r.ok?r.json():null; }).then(function(s){ var v=s&&s.version;
    if(!v||!/^[0-9a-f]{7,40}$/.test(v.commit||'')) return; var rev=/-(\\d+)-[a-z0-9]+$/.exec(v.revision||'');
    el.textContent=' \\u00b7 v'+v.commit.slice(0,7)+(rev?' \\u00b7 rev '+parseInt(rev[1],10):''); el.hidden=false; }).catch(function(){}); })();
'''
FOOT=f'''  <footer class="foot" aria-label="Footer">
    <div class="in">
      <div class="r1"><a class="wm" href="index.html"><svg><use href="#mark"/></svg>OHM</a>{a('','accountant.html','Accountant')}{a('','auditor.html','Auditor')}{a('','pricing.html','Pricing')}{a('','security.html','Security')}{a('','partners.html','Partners')}{a('',LOGIN,'Log in')}{a('','privacy.html','Privacy')}{a('','terms.html','Terms')}</div>
      <div class="r2"><span>Rohit Singh · +91 96434 98089 · rohit@yantrailabs.com</span><span>YantrAI Labs · 507 DLF Corporate Green, Sector 74A, Gurgaon 122004</span></div>
      <div class="r3">{COPY}</div>
    </div>
  </footer>
</div>
'''
SEARCH=[('Home','index.html','ohm film products problems works with tally'),('AI Accountant','accountant.html','ohm accountant bills invoice voucher bank reconciliation masters tally'),('AI Auditor','auditor.html','ohm auditor audit checks duplicate policy violations reconcile gst tds close month'),('Get anything done','command.html','command chat ask mis report reminder follow up'),('Pricing','pricing.html','price plans intern executive manager enterprise free'),('Customers','customers.html','companies logos bluestone omaxe baxy mount meru lepton odisha'),('Security','security.html','privacy secure encryption sanjaya hosting data'),('Partners','partners.html','become a partner tally earn calculator'),('Company','company.html','about ramsons yantrai vision mission team founders careers jobs contact address phone email'),('Privacy','privacy.html','privacy policy data'),('Terms','terms.html','terms of use')]
SITEJS='''(function(){
  var mb=document.getElementById('menubtn'),sh=document.getElementById('sheet');
  if(mb&&sh){ mb.addEventListener('click',function(){ var open=sh.hidden; sh.hidden=!open; mb.setAttribute('aria-expanded',open?'true':'false'); mb.textContent=open?'Close':'Menu'; }); }
  var dds=Array.prototype.slice.call(document.querySelectorAll('.dd'));
  function shut(){ dds.forEach(function(d){ d.classList.remove('open'); d.querySelector('.ddb').setAttribute('aria-expanded','false'); }); }
  dds.forEach(function(d){ var b=d.querySelector('.ddb'); b.addEventListener('click',function(e){ e.stopPropagation(); var was=d.classList.contains('open'); shut(); if(!was){ d.classList.add('open'); b.setAttribute('aria-expanded','true'); } }); });
  document.addEventListener('click',shut);
  var sb=document.getElementById('searchbtn'),sp=document.getElementById('searchp');
  if(sb&&sp){ var inp=sp.querySelector('input'),res=sp.querySelector('.res'),P=__PAGES__;
    function draw(){ var q=inp.value.trim().toLowerCase(); res.innerHTML=''; P.filter(function(p){ return !q||(p[0]+' '+p[2]).toLowerCase().indexOf(q)>-1; }).forEach(function(p){ var l=document.createElement('a'); l.href=p[1]; l.textContent=p[0]; res.appendChild(l); }); if(!res.children.length){ var s=document.createElement('span'); s.className='muted'; s.textContent='No page matches.'; res.appendChild(s); } }
    sb.addEventListener('click',function(){ sp.hidden=!sp.hidden; sb.setAttribute('aria-expanded',sp.hidden?'false':'true'); if(!sp.hidden){ draw(); inp.focus(); } });
    inp.addEventListener('input',draw);
    inp.addEventListener('keydown',function(e){ if(e.key==='Enter'){ var l=res.querySelector('a'); if(l) location.href=l.getAttribute('href'); } });
  }
  document.addEventListener('keydown',function(e){ if(e.key==='Escape'){ shut(); if(sp&&!sp.hidden){ sp.hidden=true; sb.setAttribute('aria-expanded','false'); } } });
})();
'''.replace('__PAGES__','['+','.join("['%s','%s','%s']"%p for p in SEARCH)+']')

# ---------- images ----------
src=os.path.join(REPO,'public','assets') if LIVE else os.path.expanduser('~/Desktop/memory/aifa_site/assets')
P16,P9=(f'{src}/ohm-film-poster.jpg',f'{src}/ohm-film-poster-9x16.jpg') if LIVE else ('poster16_21.jpg','poster9_21.jpg')
def data(path):
    if LIVE:
        assert os.path.isfile(path),path
        return '/assets/'+os.path.basename(path)
    mt=mimetypes.guess_type(path)[0] or 'image/png'
    return 'data:%s;base64,'%mt+base64.b64encode(open(path,'rb').read()).decode()
LOGOS={'bluestone':'mono_logo_bluestone.png','odisha':'mono_logo_odisha.png','omaxe':'mono_logo_omaxe.png','baxy':'mono_logo_baxy.png','mountmeru':'mono_logo_mountmeru.png','lepton':'mono_logo_lepton.png','baxyinfotech':'mono_logo_baxyinfotech.svg'}
ERP={'tally':'logo_tally.png','sap':'logo_sap.png','oracle':'logo_oracle.png','netsuite':'logo_netsuite.png','zohobooks':'logo_zohobooks.png','quickbooks':'logo_quickbooks.jpeg','sage':'logo_sage.png','odoo':'logo_odoo.webp','gmail':'logo_gmail.png'}
def fill(s):
    for k,f in LOGOS.items():
        if '{{LOGO_%s}}'%k in s: s=s.replace('{{LOGO_%s}}'%k,data(f'{src}/{f}'))
    for k,f in ERP.items():
        if '{{ERP_%s}}'%k in s: s=s.replace('{{ERP_%s}}'%k,data(f'{src}/{f}'))
    if '{{POSTER16}}' in s: s=s.replace('{{POSTER16}}',data(P16)).replace('{{POSTER9}}',data(P9))
    assert '{{' not in s, re.findall(r'\{\{[A-Z_a-z0-9]+\}\}',s)
    return s

# ---------- block helpers ----------
def box(n,name,pts,you=None,href=None,id_=None):
    h=a('',href,name) if href else name
    li=''.join(f'<li>{p}</li>' for p in pts)
    y=f'<div class="you"><b>You</b><span>{you}</span></div>' if you else ''
    no=f'<span class="pno">{n}</span>' if n else ''
    i=f' id="{id_}"' if id_ else ''
    return f'<div class="pbox{" lk" if href else ""}"{i}>{no}<h3>{h}</h3><ul>{li}</ul>{y}</div>'
ARSVG='<span class="ar"><svg viewBox="0 0 24 24"><path d="M4 12h15M13 6l6 6-6 6"/></svg></span>'
def flow(steps):
    out=[]
    for i,s in enumerate(steps):
        out.append(f'<div class="st{" on" if i==len(steps)-1 else ""}"><i>{i+1}</i>{s}</div>')
    return '<div class="flow">'+ARSVG.join(out)+'</div>'
def sec(title,inner,cls='',id_=None,lead=None):
    i=f' id="{id_}"' if id_ else ''
    l=f'<p class="lead">{lead}</p>' if lead else ''
    return f'  <section class="in pg {cls}"{i} aria-label="{title}">\n    <h2 class="h2">{title}</h2>{l}\n    {inner}\n  </section>\n'
def top(title,lead=None):
    l=f'<p class="lead">{lead}</p>' if lead else ''
    return f'  <section class="in pgtop"><h1 class="h2">{title}</h1>{l}</section>\n'
def slide_top(i,label):
    return f'  <section class="band" aria-label="{label}"><div class="in">\n      {SLIDES[i]}\n  </div></section>\n'
CTA=f'  <section class="pgcta" aria-label="Start"><div class="in">{START_A}{a("btn o","company.html#contact","Talk to us")}</div></section>\n'

PAGES={}
def page(fn,title,body,active='',js=''):
    PAGES[fn]=(title,header(active)+body+FOOT,js)

# ---------- home ----------
home=HOMEBODY+h1+h2
home=sub(home,START_BTN,START_A)
home=sub(home,'<span class="btn p">Start free</span>',a('btn p',SIGNUP,'Start free'),1)
home=sub(home,'<b>Companies we work with</b>',a('','customers.html','<b>Companies we work with</b>'),1)
for pid,nm,u in [('accountant','AI Accountant','accountant.html'),('auditor','AI Auditor','auditor.html'),('command','Get anything done','command.html')]:
    home=sub(home,f'<div class="pbox" id="{pid}">',f'<div class="pbox lk" id="{pid}">',1)
    home=sub(home,f'<h3>{nm}</h3>',f'<h3>{a("",u,nm)}</h3>',1)
home=sub(home,'<a class="pr" href="#signup">',f'<a class="pr" href="{SIGNUP}" target="_blank" rel="noopener">',3)
home=sub(home,'<span class="btn p sm">Get started '+ARR+'</span>',a('btn p sm',SIGNUP,'Get started '+ARR),1)
sa=home.index('<script>'); sb_=home.index('</script>')+9
homejs=home[sa+8:sb_-9]; home=home[:sa]+home[sb_:]
home=home[:home.index('  <!-- ===== Footer ===== -->')]
assert 'class="price"' not in home and '<script' not in home
page('index.html','Ohm Home Draft',home,'home',script+'\n'+homejs+(LEGACYJS if LIVE else ''))

# ---------- AI Accountant ----------
body=slide_top(0,'AI Accountant')
body+=sec('What it does','<div class="boxes">'+
 box('01','Invoice processing',['Reads any bill: printed, scanned or handwritten','Builds the voucher: type, date, party, lines','Fills ledger, cost centre and tax from your past bills','Sales and purchase'],'Approve, or correct once. Ohm remembers.')+
 box('02','Bank reconciliation',['Reads every line of the bank statement','Picks the party, bill or expense head','Payments and receipts','When unsure, says so and proposes a head'],'Accept in one click, or change it.')+
 box('03','Talks to Tally',['Syncs masters, vouchers and balances','Posts balanced vouchers','Edits and deletes only where you switch it on','Links every voucher to its bill or bank line'],'Set what Ohm may do, user by user.')+
 box('04','Masters',['Syncs ledgers, parties, items, cost centres and voucher types','Flags missing PAN or GSTIN, and duplicates','Drafts a new party or item from the bill'],'Approve before first use.')+'</div>')
body+=sec('How it works',flow(['Learns from your team','Learns your policy','Does it itself']))
body+=CTA
page('accountant.html','AI Accountant · Ohm',body,'accountant')

# ---------- AI Auditor ----------
body=slide_top(1,'AI Auditor')
body+=sec('What it does','<div class="boxes">'+
 box('01','Checks every voucher',['46 checks on every voucher','Duplicate payment','Payee bank account changed','Approval limits and cost centre','PAN, GSTIN and GST filing status, on the government portals'],'Pick which checks are on. Each one warns or blocks.')+
 box('02','Holds what fails',['Lists every check: passed, warned or blocked, with the reason','Records who raised it, who approved it, at which gate','Holds the payment until it is cleared'],'Stop the payment, block the vendor, or send it back.')+
 box('03','Reconciles',['Bank','Vendor ledgers','GSTR-2B, before you pay','TDS'],'Review the lines still waiting.')+
 box('04','Closes and certifies',['Locks the month','Rolls up the group','Keeps the trail: who did what, and when','Year-end checklist done'],'Share the books with your CA in one click.')+'</div>')
body+=sec('Your policy, at every gate',flow(['Read','Submit','Approve','Send to Tally','Pay']))
body+=sec('How it works',flow(['Finds the breach','Checks your policy','Holds the payment']))
body+=CTA
page('auditor.html','AI Auditor · Ohm',body,'auditor')

# ---------- Get anything done ----------
body=slide_top(2,'Get anything done')
body+=sec('What it does','<div class="boxes c3">'+
 box('01','Ask',['Sales','Collections','Payables','Policy'],'Ask on your phone or on the web.')+
 box('02','Answers',['Live numbers, from the books as they are now','The bills behind every number','Revenue by customer, overdue by age, suppliers paid, open violations'])+
 box('03','Acts',['Drafts the reminder','Holds the payment','Waits for your approval'],'Approve what it drafts.')+'</div>')
body+=sec('How it works',flow(['Ask in plain words','Reads the live books','Answers and acts']))
body+=CTA
chatjs='(function(){\n  var reduce=window.matchMedia&&window.matchMedia("(prefers-reduced-motion: reduce)").matches;\n'+CONVOS+CHATFN+'  chatStart(); setInterval(chatStart,7000);\n})();\n'
page('command.html','Get anything done · Ohm',body,'command',chatjs)

# ---------- Pricing ----------
pr=pricing
pr=sub(pr,'<span class="btn o">Start free</span>',a('btn o',SIGNUP,'Start free'),1)
pr=sub(pr,'<span class="btn p">Start free, upgrade any time</span>',a('btn p',SIGNUP,'Start free, upgrade any time'),1)
pr=sub(pr,'<span class="btn o">Talk to us</span>',a('btn o','company.html#contact','Talk to us'),1)
pr=sub(pr,'<span class="btn o">Reach out to us '+ARR+'</span>',a('btn o','company.html#contact','Reach out to us '+ARR),1)
pr=sub(pr,'<a class="more" href="#">See full pricing →</a>','',1)
pr=sub(pr,'<h2 class="h2">Pricing</h2>','<h1 class="h2">Pricing</h1>',1)
page('pricing.html','Pricing · Ohm',pr,'pricing')

# ---------- Customers ----------
names=[('bluestone','BlueStone'),('odisha','Government of Odisha'),('omaxe','Omaxe'),('baxy','Baxy'),('mountmeru','Mount Meru Group'),('lepton','Lepton Software'),('baxyinfotech','Baxy Infotech')]
body=top('Customers','India, Dubai, the USA and Africa')
body+='  <section class="in pg" style="border-top:0;padding-top:0" aria-label="Companies we work with"><div class="lgrid">'+''.join(f'<div><img src="{{{{LOGO_{k}}}}}" alt="{n}"><span>{n}</span></div>' for k,n in names)+'</div></section>\n'
body+=CTA
page('customers.html','Customers · Ohm',body,'customers')

# ---------- Security ----------
p3=h2[h2.index('  <!-- ===== Privacy (data path) ===== -->'):h2.index('  <!-- ===== Works with ===== -->')]
p3=sub(p3,'<h2 class="h2">Privacy and security</h2>','<h1 class="h2">Security</h1>',1)
p3=sub(p3,'<section class="in p3" id="privacy"','<section class="in p3" style="border-top:0" id="privacy"',1)
ic=lambda d:f'<i><svg viewBox="0 0 24 24">{d}</svg></i>'
sig='<div class="sig">'+''.join(f'<div>{ic(d)}<b>{b}</b><span>{s}</span></div>' for d,b,s in [
 ('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>','Encrypted','in transit and at rest, 256-bit'),
 ('<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/>','Walled off','from every other company'),
 ('<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>','Named users only','each sees only their job'),
 ('<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>','Every action logged','who, what and when'),
 ('<rect x="4" y="4" width="16" height="7" rx="2"/><rect x="4" y="13" width="16" height="7" rx="2"/><path d="M8 7.5h.01M8 16.5h.01"/>','Offline and on cloud','on your own server, or on our cloud'),
 ('<rect x="7" y="7" width="10" height="10" rx="2"/><path d="M10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4"/>','No LLM training','on your data'),
 ('<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z"/><path d="M5 17a3 3 0 0 1 3-3h11"/>','Tally keeps the books','Ohm never replaces it')])+'</div>'
qa=[('Where does the data sit?','On the Ohm cloud in India, a private cloud, or your own servers. Walled off from every other company, encrypted in transit and at rest. Tally remains your book of record.'),
    ('Who can see it?','Only your named users, each by role. No shared logins. Ohm can do only what the signed-in user is allowed to do.'),
    ('What is kept?','What you post and what Ohm writes, with a trail of what was checked, what was found and who released it. Exportable or deletable on request.'),
    ('Does any of it train a model?','No. Bills are read by Sanjaya, our own model. Nothing from your books trains a shared model.')]
faq='<div class="faq2">'+''.join(f'<details{" open" if i==0 else ""}><summary>{q}</summary><p>{t}</p></details>' for i,(q,t) in enumerate(qa))+'</div>'
body=p3+sec('Safeguards',sig,'')+sec('Questions',faq)+CTA
page('security.html','Security · Ohm',body,'security')

# ---------- Partners ----------
pt=ph
pt=sub(pt,'<span class="btn p">Become a partner '+ARR+'</span>',a('btn p','#join','Become a partner '+ARR),2)
pt=sub(pt,'<span class="btn">Become a partner '+ARR+'</span>',a('btn','#join','Become a partner '+ARR),1)
pt=sub(pt,'<section class="pp-cta" aria-label="Become a partner">','<section class="pp-cta" id="join" aria-label="Become a partner">',1)
pt=sub(pt,'<h2 class="h2">Sell Ohm next to Tally. Earn every year.</h2>','<h1 class="h2">Partners</h1>',1)
pt=sub(pt,'<div class="k">Partners</div>','',1)
pt=sub(pt,'<p class="lead">Ohm sits on your customer\'s Tally. You bring the customer; Ohm pays you for as long as they stay.</p>','',1)
for k in ['Earning potential','Questions partners ask']: pt=sub(pt,f'<div class="k">{k}</div>','',1)
pt=sub(pt,'<h2 class="h2">Your customers, with Ohm.</h2>','<h2 class="h2">Earning potential</h2>',1)
pt=sub(pt,'<h2 class="h2">The short answers.</h2>','<h2 class="h2">Questions</h2>',1)
pt=sub(pt,'<h2 class="h2">Bring your first customer this week.</h2>','<h2 class="h2">Become a partner</h2>',1)
pt=sub(pt,'<span class="btn p">Become a partner '+ARR+'</span><span class="muted">or talk to Rohit · +91 96434 98089 · rohit@yantrailabs.com</span>' if False else '<span class="muted">or talk to Rohit · +91 96434 98089 · rohit@yantrailabs.com</span>','<span class="sel">Rohit Singh · +91 96434 98089 · rohit@yantrailabs.com</span>',1)
page('partners.html','Partners · Ohm','<div class="pp">\n'+pt+'\n</div>\n','partners',pj)

# ---------- Company ----------
body=top('Company')
body+=sec('About','<div class="stats"><div><b>27</b><span>years in business</span></div><div><b>₹500 Cr+</b><span>combined turnover</span></div><div><b>4</b><span>verticals</span></div></div><div class="chips"><span>Auto-components</span><span>e-Rickshaw</span><span>Tech platforms</span><span>D2C</span></div>','', 'about','Ohm is built by YantrAI Labs, part of Ramsons group.').replace('border-top:1px','border-top:1px')
body+=sec('Vision and mission','<div class="boxes"><div class="pbox plain"><div class="lbl">Vision</div><p class="bigl">An agent for every work.</p></div><div class="pbox plain"><div class="lbl">Mission</div><p class="bigl">Build agents that make humans 10×.</p></div></div>'
 '<div class="boxes c3" style="margin-top:16px">'+''.join(f'<div class="pbox plain"><h3>{t}</h3><p>{d}</p></div>' for t,d in [
  ('Objective','We go by evidence, not opinion. Every answer can be traced to its source, and the same input always gives the same result.'),
  ('Honest','We say what we know, what we do not, and when we are unsure. A doubt is never hidden behind a confident answer.'),
  ('Methodical','We do the work the same careful way every time: every step, every check, in order, with no shortcuts.')])+'</div>')
def person(name,role,pts):
    return f'<div class="pbox"><h3>{name}</h3><div class="role" style="margin-top:10px">{role}</div><ul>'+''.join(f'<li>{p}</li>' for p in pts)+'</ul></div>'
body+=sec('Team','<div class="boxes">'+
 person('Mohit','Co-founder and CEO · Tech and product',['Oye! Rickshaw: co-founder and product lead; 100,000+ transactions a day, $12M raised','Jangid Motors: co-founder, tech and product; IoT e-rickshaw, $5M ARR','Hero MotoCorp: supply chain; $200M purchase portfolio','IIT Delhi, B.Tech, 2012'])+
 person('Rohit','Co-founder · Revenue and operations',['WayCool: strategy; ran the retail vertical, scaled to about $10M','EasyLokal: co-founder; B2B channel into Reliance and Zepto, acquired by WayCool','Zomato: head of operations; 0 to 22,000 orders a day across 6+ cities','NIT Rourkela, B.Tech, 2012'])+'</div>','','team')
body+=sec('Careers','<div class="boxes"><div class="pbox plain"><p style="color:var(--ink);font-size:16px">Write to a founder: <span class="sel">rohit@yantrailabs.com</span></p></div></div>','','careers','We do not post roles. We add someone when we meet a person who makes the next year better.')
body+=sec('Contact','<div class="boxes contact2"><div class="pbox plain"><h3>Rohit Singh</h3><p><span class="sel">+91 96434 98089</span><br><span class="sel">rohit@yantrailabs.com</span></p></div><div class="pbox plain"><h3>YantrAI Labs</h3><p>507 DLF Corporate Green<br>Sector 74A, Gurgaon 122004</p></div></div>','','contact')
page('company.html','Company · Ohm',body,'company')

# ---------- Privacy, Terms (drafts) ----------
def legal(title,blocks):
    inner=''.join(f'<h3>{h}</h3>'+(''.join(f'<p>{x}</p>' for x in b) if isinstance(b,tuple) else '<ul>'+''.join(f'<li>{x}</li>' for x in b)+'</ul>') for h,b in blocks)
    return f'  <section class="in pgtop legal"><h1 class="h2">{title}</h1><span class="draft">Draft, pending legal review</span>{inner}</section>\n'
body=legal('Privacy',[
 ('What we collect',['Your account details: name, email, phone and company','The bills, bank statements and vouchers you upload or send','The masters, vouchers and balances synced from your Tally','A log of who did what, and when']),
 ('How we use it',['To read bills, draft vouchers, run checks and post to Tally, on your instruction','To answer the questions you ask','To bill you and to support you','Never to train a shared model, and never sold']),
 ('Where it sits',['On the Ohm cloud in India, a private cloud, or your own servers','Encrypted in transit and at rest','Walled off from every other company']),
 ('Who can see it',['Your named users, each by role','Ohm staff, only to support you']),
 ('Keeping and deleting',['Kept while your account is active','Exported or deleted on your request']),
 ('Contact',('YantrAI Labs · 507 DLF Corporate Green, Sector 74A, Gurgaon 122004 · rohit@yantrailabs.com',))])
page('privacy.html','Privacy · Ohm',body,'')
body=legal('Terms',[
 ('The service',['Ohm reads bills and bank statements, drafts vouchers, runs checks and posts to Tally','Tally remains your book of record','Nothing is posted or paid without a person approving it']),
 ('Your account',['Named users only; no shared logins','You decide what each user, and Ohm, may do']),
 ('Plans and payment',['Plans are by new transactions a month, as on the Pricing page','Paid monthly in advance','A GST tax invoice for every payment']),
 ('Your data',['Your books stay yours','Exportable or deletable on request','Handled as set out on the Privacy page']),
 ('Ending',['You may stop at any time; the plan runs to the end of the paid month']),
 ('Law',['These terms are governed by the laws of India']),
 ('Contact',('YantrAI Labs · 507 DLF Corporate Green, Sector 74A, Gurgaon 122004 · rohit@yantrailabs.com',))])
page('terms.html','Terms · Ohm',body,'')

# ---------- write ----------
def live_links(s):
    pat=r"""(["'])(%s)(#[\w-]+)?\1"""%'|'.join(re.escape(k) for k in ROUTE)
    s=re.sub(pat,lambda m:m.group(1)+ROUTE[m.group(2)]+(m.group(3) or '')+m.group(1),s)
    for k,v in VIDEO.items(): s=s.replace(k,v)
    return s
def live_head(fn):
    title,desc=META[fn]; url=SITE+ROUTE[fn]; slug=ROUTE[fn].strip('/')
    e=lambda t:t.replace('&','&amp;').replace('"','&quot;')
    h=[f'<title>{title}</title>',f'<meta name="description" content="{e(desc)}">',f'<link rel="canonical" href="{url}">',
       '<meta name="theme-color" content="#FFFFFF">','<meta name="robots" content="index, follow">',
       '<meta property="og:type" content="website">','<meta property="og:site_name" content="YantrAI">',
       f'<meta property="og:title" content="{e(title)}">',f'<meta property="og:description" content="{e(desc)}">',f'<meta property="og:url" content="{url}">',
       '<meta name="twitter:card" content="summary">',
       '<link rel="icon" href="/favicon.ico" sizes="32x32">','<link rel="icon" href="/brand/ohm/ohm-mark.svg" type="image/svg+xml">','<link rel="apple-touch-icon" href="/apple-touch-icon.png">',
       '<link rel="preconnect" href="https://fonts.googleapis.com">','<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>']
    # the French site keeps its own pages; name the pair where one exists
    if os.path.isfile(os.path.join(OUT,'fr',slug,'index.html')):
        fr=SITE+'/fr/'+slug if slug else SITE+'/fr/'
        h+=[f'<link rel="alternate" hreflang="en" href="{url}">',f'<link rel="alternate" hreflang="fr" href="{fr}">',f'<link rel="alternate" hreflang="x-default" href="{url}">']
    return '\n'.join(h)+'\n'
def doc(title,body,js,full=True,fn=None):
    live=LIVE and fn
    links='\n'.join(l for l in HEADLINKS.splitlines() if 'claude:' not in l) if live else HEADLINKS   # the claude: meta only means something inside an artifact
    head=(live_head(fn) if live else f'<title>{title}</title>\n')+f'{links}\n<style>{CSS}</style>\n'
    scripts='\n<script>\n'+SITEJS+(js or '')+(VERSIONJS if live else '')+'\n</script>\n'
    inner=head+SYMBOLS+'\n\n'+body+scripts
    if not full: return inner
    out='<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'+head+'</head>\n<body>\n'+SYMBOLS+'\n\n'+body+scripts+'</body>\n</html>\n'
    return live_links(out) if live else out
written={}
for fn,(title,body,js) in PAGES.items():
    if LIVE and fn in LIVE_SKIP: continue
    body=fill(body)
    d=doc(title,body,js,fn=fn)
    if LIVE:
        slug=ROUTE[fn].strip('/'); path=os.path.join(OUT,slug,'index.html') if slug else os.path.join(OUT,'index.html')
        os.makedirs(os.path.dirname(path),exist_ok=True)
    else:
        path=os.path.join(OUT,fn)
    open(path,'w',encoding='utf-8').write(d); written[fn]=d
    if fn=='index.html' and not LIVE: open('home_v11.html','w',encoding='utf-8').write(doc(title,body,js,full=False))
    print(os.path.relpath(path,OUT),len(d)//1024,'KB')
if LIVE:
    # every address a page points at must exist in public/
    bad=set()
    for fn,d in written.items():
        for h in re.findall(r"""(?:href|src|poster|data-poster-[a-z]+)=["'](/[^"'#?]*)""",d)+re.findall(r"""['"](/assets/[^'"]+)['"]""",d):
            t=os.path.join(OUT,h.strip('/'))
            if not (h=='/' or os.path.isfile(t) or os.path.isfile(os.path.join(t,'index.html'))): bad.add((fn,h))
        assert '.html"' not in d and "video/" not in d and 'data:image' not in d,fn
    assert not bad,sorted(bad)
    print('links ok'); raise SystemExit
v=os.path.join(OUT,'video')
if not os.path.exists(v): os.symlink(os.path.join(HERE,'video'),v)
# every internal link must resolve
names=set(PAGES); bad=set()
for fn,(t,b,j) in PAGES.items():
    for h in re.findall(r'href="([^"#][^"]*)"',b):
        if h.startswith('http'): continue
        if h.split('#')[0] not in names: bad.add((fn,h))
assert not bad,bad
print('links ok')
