/* Shared by all pages: header, phone tab bar, language (EN/PL), install-as-app, API helper, gift-code tickets. */
(function () {
  const C = window.WOS_CONFIG || {};
  const T = {
    en: { nav_home: 'Home', nav_report: 'Report a bug', nav_known: 'Known issues', nav_fixed: "What's fixed", nav_codes: 'Gift codes', nav_my: 'My reports', tab_my: 'Mine', langLabel: 'Language',
      tab_home: 'Home', tab_codes: 'Codes', tab_known: 'Issues', tab_report: 'Report', tab_fixed: 'Fixed',
      newBadge: 'NEW', discord: 'Join our Discord', status: 'Server status', skip: 'Skip to content', rights: 'Made for the players of',
      netErr: 'Could not reach the server. Please try again later.', install: 'Install app', copy: 'Copy', copied: 'Copied',
      left: 'Expires in', never: 'Never expires', d: 'd', h: 'h', m: 'min',
      iosHint: 'On iPhone: tap the Share button, then “Add to Home Screen”.', offline: 'You are offline – showing what was saved last time.',
      bellOn: 'Notify me', bellIsOn: 'Notifications on', bellOff: 'Turn off notifications', bellDenied: 'Notifications are blocked for this site – allow them in your browser settings.',
      bellIOS: 'On iPhone, add the app to your Home Screen first (Share → Add to Home Screen), then turn notifications on there.',
      bellNo: "This browser can't show notifications.", bellHttps: 'Notifications need the secure (https) version of this site.', bellDone: "Done – you'll get a notification for new codes." },
    pl: { nav_home: 'Start', nav_report: 'Zgłoś błąd', nav_known: 'Znane problemy', nav_fixed: 'Co naprawiono', nav_codes: 'Kody', nav_my: 'Moje zgłoszenia', tab_my: 'Moje', langLabel: 'Język',
      tab_home: 'Start', tab_codes: 'Kody', tab_known: 'Problemy', tab_report: 'Zgłoś', tab_fixed: 'Poprawki',
      newBadge: 'NOWE', discord: 'Dołącz do Discorda', status: 'Status serwera', skip: 'Przejdź do treści', rights: 'Stworzone dla graczy',
      netErr: 'Nie można połączyć się z serwerem. Spróbuj później.', install: 'Zainstaluj aplikację', copy: 'Kopiuj', copied: 'Skopiowano',
      left: 'Wygasa za', never: 'Nie wygasa', d: 'd', h: 'godz.', m: 'min',
      iosHint: 'Na iPhonie: stuknij Udostępnij, a potem „Do ekranu początkowego”.', offline: 'Jesteś offline – pokazujemy ostatnio zapisane dane.',
      bellOn: 'Powiadamiaj mnie', bellIsOn: 'Powiadomienia włączone', bellOff: 'Wyłącz powiadomienia', bellDenied: 'Powiadomienia są zablokowane dla tej strony – zezwól na nie w ustawieniach przeglądarki.',
      bellIOS: 'Na iPhonie najpierw dodaj aplikację do ekranu początkowego (Udostępnij → Do ekranu początkowego), a potem włącz tam powiadomienia.',
      bellNo: 'Ta przeglądarka nie obsługuje powiadomień.', bellHttps: 'Powiadomienia działają tylko w bezpiecznej (https) wersji strony.', bellDone: 'Gotowe – dostaniesz powiadomienie o nowych kodach.' }
  };
  const ICON = {
    home: '<path d="M3 11.5 12 4l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    codes: '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13M19 12v9H5v-9M7.5 8a2.5 2.5 0 0 1 0-5C10 3 12 8 12 8s2-5 4.5-5a2.5 2.5 0 0 1 0 5"/>',
    known: '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
    report: '<rect x="8" y="7" width="8" height="12" rx="4"/><path d="M12 7V4M8 11H4M16 11h4M8 16H5M16 16h3M9.5 4.5 8 3M14.5 4.5 16 3"/>',
    fixed: '<path d="M20 6 9 17l-5-5"/>',
    download: '<path d="M12 4v12M7 11l5 5 5-5M4 20h16"/>',
    chat: '<path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
    my: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/>'
  };
  // Language: the one picked before, else the browser's if the server offers it (checked once the list is known), else English.
  const RTL = ['ar', 'fa', 'he', 'ur'];
  const store = { get: k => { try { return localStorage.getItem(k); } catch (e) { return null; } }, set: (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } } };
  const browser = (navigator.language || 'en').toLowerCase();
  let lang = store.get('wos.lang') || (browser.startsWith('pl') ? 'pl' : 'en');
  const pages = [['index.html', 'home'], ['codes.html', 'codes'], ['known-issues.html', 'known'], ['report.html', 'report'], ['changelog.html', 'fixed'], ['my.html', 'my']];
  const pageKey = (location.pathname.split('/').pop() || 'index.html').replace(/\.html$/, '') || 'index';
  let remote = {};                       // texts from the server for the current language (Translations page on the dashboard)
  try { remote = JSON.parse(store.get('wos.tx.' + lang) || '{}'); } catch (e) { remote = {}; }
  const W = window.WOS = { cfg: {}, lang, strings: {}, onLang: [] };
  W.t = k => remote[pageKey + '.' + k] || remote['app.' + k] || (W.strings[W.lang] && W.strings[W.lang][k]) || (T[W.lang] && T[W.lang][k]) || (W.strings.en && W.strings.en[k]) || T.en[k] || k;
  W.locale = () => ({ en: 'en-GB', 'zh-cn': 'zh-CN', 'pt-br': 'pt-BR', no: 'nb' }[W.lang] || W.lang);
  // Fetch the texts of a language from the bot; the last copy is kept on the device so pages paint in the right language straight away.
  W.loadTexts = async function (code) {
    try { const d = await W.api('/public/texts?lang=' + encodeURIComponent(code)); store.set('wos.tx.' + code, JSON.stringify(d.texts)); if (W.lang === code) remote = d.texts; }
    catch (e) { if (W.lang === code) { try { remote = JSON.parse(store.get('wos.tx.' + code) || '{}'); } catch (x) { remote = {}; } } }
  };
  W.setLang = async function (code, save) {
    W.lang = code; if (save) store.set('wos.lang', code);
    try { remote = JSON.parse(store.get('wos.tx.' + code) || '{}'); } catch (e) { remote = {}; }
    W.apply(); await W.loadTexts(code); W.apply();
  };
  W.icon = n => { const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('fill', 'none');
    s.setAttribute('stroke', 'currentColor'); s.setAttribute('stroke-width', '1.8'); s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round');
    s.setAttribute('aria-hidden', 'true'); s.innerHTML = ICON[n] || ''; return s; };
  W.api = async (path, opts) => {
    const r = await fetch((C.API_BASE || '').replace(/\/+$/, '') + path, opts);
    let j = {}; try { j = await r.json(); } catch (e) { /* not json */ }
    if (!r.ok) { const e = new Error(j.error || W.t('netErr')); e.status = r.status; throw e; }
    return j;
  };
  W.el = (tag, props, ...kids) => { const e = document.createElement(tag); Object.entries(props || {}).forEach(([k, v]) => { if (k === 'class') e.className = v; else if (k.startsWith('on')) e[k] = v; else e.setAttribute(k, v); }); kids.flat().forEach(k => e.append(k)); return e; };
  W.date = ts => { const o = { day: 'numeric', month: 'short', year: 'numeric' }, d = new Date(ts * 1000); try { return d.toLocaleDateString(W.locale(), o); } catch (e) { return d.toLocaleDateString('en-GB', o); } };
  W.apply = function () {
    document.documentElement.lang = W.lang; document.documentElement.dir = RTL.includes(W.lang.split('-')[0]) ? 'rtl' : 'ltr';
    document.querySelectorAll('[data-i]').forEach(e => { e.textContent = W.t(e.dataset.i); });
    document.querySelectorAll('[data-ph]').forEach(e => { e.placeholder = W.t(e.dataset.ph); });
    const ls = document.getElementById('lang-sel'); if (ls) { ls.value = W.lang; ls.setAttribute('aria-label', W.t('langLabel')); }
    W.onLang.forEach(f => f());
  };

  /* ---- gift codes, shared by the home page and the codes page ---- */
  W.left = ts => {
    if (!ts) return W.t('never');
    const s = Math.max(0, ts - Date.now() / 1000), d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60);
    return W.t('left') + ' ' + (d ? d + ' ' + W.t('d') + ' ' + h + ' ' + W.t('h') : h ? h + ' ' + W.t('h') + ' ' + m + ' ' + W.t('m') : m + ' ' + W.t('m'));
  };
  W.copy = async (text, btn) => {
    try { await navigator.clipboard.writeText(text); }
    catch (e) { const t = document.createElement('textarea'); t.value = text; t.setAttribute('readonly', ''); t.style.position = 'fixed'; t.style.opacity = '0'; document.body.append(t); t.select(); try { document.execCommand('copy'); } catch (x) { /* ignore */ } t.remove(); }
    if (navigator.vibrate) navigator.vibrate(12);
    btn.replaceChildren(W.icon('fixed'), W.t('copied')); btn.classList.add('ok');
    clearTimeout(btn._t); btn._t = setTimeout(() => { btn.replaceChildren(W.t('copy')); btn.classList.remove('ok'); }, 1600);
  };
  W.ticket = (c, small) => {
    const b = W.el('button', { class: 'btn copy', type: 'button', 'aria-label': W.t('copy') + ' ' + c.code }, W.t('copy')); b.onclick = () => W.copy(c.code, b);
    const cv = W.el('div', { class: 'cv' }, c.code); if (c.new) cv.append(W.el('span', { class: 'tag' }, W.t('newBadge')));
    return W.el('div', { class: 'ticket' + (c.new ? ' new' : ' quiet') + (small ? ' sm' : '') }, ...(c.new ? [W.el('i', { class: 'shine', 'aria-hidden': 'true' })] : []),
      W.el('div', {}, cv, ...(c.reward ? [W.el('div', { class: 'rw' }, c.reward)] : []), W.el('div', { class: 'ex' }, W.left(c.expires))), b);
  };
  W.seenCodes = () => { try { return parseInt(localStorage.getItem('wos.codesSeen') || '0', 10); } catch (e) { return 0; } };
  W.markCodesSeen = ts => { try { localStorage.setItem('wos.codesSeen', String(ts)); } catch (e) { /* ignore */ } };
  W.codesBadge = async function () {
    try {
      const d = await W.api('/public/giftcodes'), seen = W.seenCodes();
      if (d.items.some(c => c.added > seen)) document.querySelectorAll('[data-k="codes"]').forEach(a => { a.dataset.new = '1'; });
    } catch (e) { /* codes off or offline */ }
  };
  // Numbers count up once when they appear (skipped for people who prefer less motion).
  W.tick = el => {
    const to = +el.textContent; if (!to || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const t0 = performance.now(); const step = n => { const p = Math.min(1, (n - t0) / 800); el.textContent = String(Math.round(to * (1 - Math.pow(1 - p, 3)))); if (p < 1) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  };

  /* ---- phone notifications ---- */
  const b64 = s => { const p = '='.repeat((4 - s.length % 4) % 4), r = atob((s + p).replace(/-/g, '+').replace(/_/g, '/')); return Uint8Array.from([...r].map(c => c.charCodeAt(0))); };
  W.pushSupported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  W.pushState = async () => {
    if (!W.pushSupported()) return false;
    try { const reg = await navigator.serviceWorker.getRegistration(); const sub = reg && await reg.pushManager.getSubscription(); return !!sub; } catch (e) { return false; }
  };
  W.pushOn = async () => {
    if (location.protocol !== 'https:' && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1') throw new Error(W.t('bellHttps'));
    if (isIOS() && !standalone()) throw new Error(W.t('bellIOS'));
    if (!W.pushSupported()) throw new Error(W.t('bellNo'));
    const k = await W.api('/public/push/key'); if (!k.enabled) throw new Error(W.t('bellNo'));
    const perm = await Notification.requestPermission(); if (perm !== 'granted') throw new Error(W.t('bellDenied'));
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64(k.key) });
    await W.api('/public/push/subscribe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ subscription: sub.toJSON(), topics: ['codes', 'news'], lang: W.lang }) });
  };
  W.pushOff = async () => {
    const reg = await navigator.serviceWorker.getRegistration(); const sub = reg && await reg.pushManager.getSubscription();
    if (sub) { const ep = sub.endpoint; await sub.unsubscribe(); try { await W.api('/public/push/unsubscribe', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ endpoint: ep }) }); } catch (e) { /* ignore */ } }
  };
  // A "Notify me" button that shows its own state and explains what to do when it can't work.
  W.bell = () => {
    const btn = W.el('button', { class: 'btn pri bell', type: 'button' }), note = W.el('div', { class: 'ios', role: 'status' });
    let on = false;
    const draw = () => { btn.classList.toggle('pri', !on); btn.replaceChildren(W.icon('bell'), on ? W.t('bellIsOn') + ' ✓' : W.t('bellOn')); btn.title = on ? W.t('bellOff') : ''; };
    btn.onclick = async () => {
      btn.disabled = true; note.textContent = '';
      try { if (on) { await W.pushOff(); on = false; } else { await W.pushOn(); on = true; note.textContent = W.t('bellDone'); } }
      catch (e) { note.textContent = e.message; }
      btn.disabled = false; draw();
    };
    W.pushState().then(v => { on = v; draw(); }); draw(); W.onLang.push(draw);
    return W.el('div', { class: 'bellbox' }, btn, note);
  };

  /* ---- install as an app ---- */
  let deferred = null;
  const standalone = () => matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const isIOS = () => /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  W.canInstall = () => !standalone() && (!!deferred || isIOS());
  W.install = async () => {
    if (deferred) { deferred.prompt(); try { await deferred.userChoice; } catch (e) { /* ignore */ } deferred = null; W.syncInstall(); return true; }
    if (isIOS()) { alertIOS(); return true; }
    return false;
  };
  function alertIOS() {
    let n = document.getElementById('ios-hint');
    if (!n) { n = W.el('div', { class: 'card ios', id: 'ios-hint', role: 'status', style: 'position:fixed;left:12px;right:12px;bottom:calc(var(--tabbar) + 12px);z-index:70' }, W.t('iosHint')); document.body.append(n); }
    n.hidden = false; setTimeout(() => { n.hidden = true; }, 6000);
  }
  W.syncInstall = () => document.querySelectorAll('[data-install]').forEach(b => { b.hidden = !W.canInstall(); });
  window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); deferred = e; W.syncInstall(); });
  window.addEventListener('appinstalled', () => { deferred = null; W.syncInstall(); });
  if ('serviceWorker' in navigator && location.protocol !== 'file:') {
    window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => { /* not available */ }));
  }

  const langOptions = list => (list.some(l => l.code === lang) ? list : list.concat([{ code: lang, name: lang.toUpperCase() }])).map(l => { const o = document.createElement('option'); o.value = l.code; o.textContent = l.name; return o; });
  function chrome() {
    const here = (location.pathname.split('/').pop() || 'index.html');
    const cur = f => (f === here || (here === '' && f === 'index.html')) ? { 'aria-current': 'page' } : {};
    const links = pages.map(([f, k]) => W.el('a', { class: 'lnk', href: f, 'data-i': 'nav_' + k, 'data-k': k, id: 'nav-' + k, ...cur(f) }));
    const nav = W.el('nav', { class: 'nav', id: 'nav', 'aria-label': 'Main' }, links);
    const tools = W.el('div', { class: 'hdr-tools' },
      W.el('button', { class: 'pill install', type: 'button', 'data-install': '', 'data-i': 'install', hidden: '', onclick: () => W.install() }),
      W.el('select', { class: 'pill lang', id: 'lang-sel' }, langOptions([{ code: 'en', name: 'English' }, { code: 'pl', name: 'Polski' }])));
    const logo = W.el('img', { class: 'logo', src: 'icons/icon-192.png', alt: '', width: '34', height: '34' });
    const hdr = W.el('header', { class: 'hdr' }, W.el('div', { class: 'hdr-in' },
      W.el('a', { class: 'brand', href: 'index.html' }, logo, W.el('span', {}, C.SERVER_NAME || 'WOS')), nav, tools));
    const tabs = W.el('nav', { class: 'tabs', 'aria-label': 'Main' }, pages.map(([f, k]) =>
      W.el('a', { class: 'tab', href: f, 'data-k': k, id: 'tab-' + k, ...cur(f) }, W.icon(k), W.el('span', { 'data-i': 'tab_' + k }))));
    document.body.prepend(W.el('a', { class: 'skip', href: '#main', 'data-i': 'skip' }), hdr);
    const ftr = W.el('footer', {}, W.el('div', { class: 'ftr' }, W.el('span', {}, W.t('rights') + ' ' + (C.SERVER_NAME || '')),
      W.el('nav', {}, ...(C.STATUS_URL ? [W.el('a', { href: C.STATUS_URL, 'data-i': 'status' })] : []), ...(C.DISCORD_URL ? [W.el('a', { href: C.DISCORD_URL, rel: 'noopener', 'data-i': 'discord' })] : []))));
    hdr.append(tabs);                 // on phones the tabs sit in the header at the top (the browser's bar covers the bottom)
    document.body.append(ftr);
    document.getElementById('lang-sel').onchange = e => W.setLang(e.target.value, true);
    const m = document.querySelector('main'); if (m) m.id = 'main';
    // a little snow, never for people who prefer less motion
    if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
      const snow = W.el('div', { class: 'snow', 'aria-hidden': 'true' });
      for (let i = 0; i < 26; i++) {
        const f = W.el('i'); const r = (a, b) => a + Math.random() * (b - a);
        f.style.cssText = `left:${r(0, 100)}%;--s:${r(1.5, 3.6).toFixed(1)}px;--d:${r(11, 24).toFixed(1)}s;--w:-${r(0, 24).toFixed(1)}s;--x:${r(-60, 60).toFixed(0)}px;opacity:${r(.25, .6).toFixed(2)}`;
        snow.append(f);
      }
      document.body.prepend(snow);
    }
    // spotlight that follows the pointer over cards
    let lit = null;
    document.addEventListener('pointermove', e => {
      const c = e.target.closest && e.target.closest('.card'); if (lit && lit !== c) lit.classList.remove('lit');
      if (!c) { lit = null; return; }
      const r = c.getBoundingClientRect(); c.style.setProperty('--mx', (e.clientX - r.left) + 'px'); c.style.setProperty('--my', (e.clientY - r.top) + 'px'); c.classList.add('lit'); lit = c;
    }, { passive: true });
  }

  W.init = async function (strings) {
    W.strings = strings || {}; chrome(); W.apply(); W.syncInstall();
    try { W.cfg = await W.api('/public/config'); } catch (e) { W.cfg = {}; }
    const langs = W.cfg.languages && W.cfg.languages.length ? W.cfg.languages : [{ code: 'en', name: 'English' }, { code: 'pl', name: 'Polski' }];
    const has = c => langs.some(l => l.code === c);
    if (!store.get('wos.lang')) {                    // first visit: the browser's language if the server has it
      const pick = has(browser) ? browser : has(browser.split('-')[0]) ? browser.split('-')[0] : (langs.find(l => l.code.split('-')[0] === browser.split('-')[0]) || {}).code;
      if (pick) W.lang = pick;
    }
    if (!has(W.lang)) W.lang = 'en';
    const sel = document.getElementById('lang-sel'); if (sel) sel.replaceChildren(...langOptions(langs));
    await W.setLang(W.lang, false);
    if (!W.cfg.player_login) document.querySelectorAll('[data-k="my"]').forEach(a => { a.hidden = true; });
    const hide = k => document.querySelectorAll(`[data-k="${k}"]`).forEach(a => { a.hidden = true; });
    if (W.cfg.known_issues === false) hide('known');
    if (W.cfg.changelog === false) hide('fixed');
    if (!W.cfg.giftcodes) hide('codes');
    else if (!/codes\.html$/.test(location.pathname)) W.codesBadge();
    const tabs = document.querySelector('.tabs'); if (tabs) tabs.style.setProperty('--n', String(tabs.querySelectorAll('.tab:not([hidden])').length));
    return W.cfg;
  };
})();
