/* Shared by all pages: navigation, language (EN/PL), API helper. */
(function () {
  const C = window.WOS_CONFIG || {};
  const T = {
    en: { nav_home: 'Home', nav_report: 'Report a bug', nav_known: 'Known issues', nav_fixed: "What's fixed", discord: 'Join our Discord', status: 'Server status', menu: 'Menu',
      skip: 'Skip to content', rights: 'Made for the players of', netErr: 'Could not reach the server. Please try again later.' },
    pl: { nav_home: 'Start', nav_report: 'Zgłoś błąd', nav_known: 'Znane problemy', nav_fixed: 'Co naprawiono', discord: 'Dołącz do Discorda', status: 'Status serwera', menu: 'Menu',
      skip: 'Przejdź do treści', rights: 'Stworzone dla graczy', netErr: 'Nie można połączyć się z serwerem. Spróbuj później.' }
  };
  let lang = 'en';
  try { lang = localStorage.getItem('wos.lang') || ((navigator.language || '').toLowerCase().startsWith('pl') ? 'pl' : 'en'); } catch (e) { /* ignore */ }
  const pages = [['index.html', 'home'], ['report.html', 'report'], ['known-issues.html', 'known'], ['changelog.html', 'fixed']];
  const W = window.WOS = { cfg: {}, lang, strings: {}, onLang: [] };
  W.t = k => (W.strings[W.lang] && W.strings[W.lang][k]) || (T[W.lang] && T[W.lang][k]) || (W.strings.en && W.strings.en[k]) || T.en[k] || k;
  W.api = async (path, opts) => {
    const r = await fetch((C.API_BASE || '').replace(/\/+$/, '') + path, opts);
    let j = {}; try { j = await r.json(); } catch (e) { /* not json */ }
    if (!r.ok) { const e = new Error(j.error || W.t('netErr')); e.status = r.status; throw e; }
    return j;
  };
  W.el = (tag, props, ...kids) => { const e = document.createElement(tag); Object.entries(props || {}).forEach(([k, v]) => { if (k === 'class') e.className = v; else if (k.startsWith('on')) e[k] = v; else e.setAttribute(k, v); }); kids.flat().forEach(k => e.append(k)); return e; };
  W.date = ts => new Date(ts * 1000).toLocaleDateString(W.lang === 'pl' ? 'pl-PL' : 'en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
  W.apply = function () {
    document.documentElement.lang = W.lang;
    document.querySelectorAll('[data-i]').forEach(e => { e.textContent = W.t(e.dataset.i); });
    document.querySelectorAll('[data-ph]').forEach(e => { e.placeholder = W.t(e.dataset.ph); });
    const lb = document.getElementById('lang-btn'); if (lb) { lb.textContent = W.lang === 'en' ? 'PL' : 'EN'; lb.setAttribute('aria-label', W.lang === 'en' ? 'Polski' : 'English'); }
    W.onLang.forEach(f => f());
  };
  function chrome() {
    const here = (location.pathname.split('/').pop() || 'index.html');
    const links = pages.map(([f, k]) => W.el('a', { class: 'lnk', href: f, 'data-i': 'nav_' + k, id: 'nav-' + k, ...(f === here || (here === '' && f === 'index.html') ? { 'aria-current': 'page' } : {}) }));
    const nav = W.el('nav', { class: 'nav', id: 'nav', 'aria-label': 'Main' }, links, W.el('button', { class: 'lang', id: 'lang-btn', type: 'button' }));
    const burger = W.el('button', { class: 'burger', type: 'button', 'aria-label': 'Menu', 'aria-expanded': 'false', 'aria-controls': 'nav' }, '☰');
    burger.onclick = () => { const o = nav.classList.toggle('open'); burger.setAttribute('aria-expanded', String(o)); burger.textContent = o ? '✕' : '☰'; };
    const hdr = W.el('header', { class: 'hdr' }, W.el('div', { class: 'hdr-in' },
      W.el('a', { class: 'brand', href: 'index.html' }, W.el('span', { class: 'logo' }, 'WOS'), W.el('span', {}, C.SERVER_NAME || 'WOS')), nav, burger));
    document.body.prepend(hdr, W.el('a', { class: 'skip', href: '#main', 'data-i': 'skip' }));
    const ftr = W.el('footer', {}, W.el('div', { class: 'ftr' }, W.el('span', {}, W.t('rights') + ' ' + (C.SERVER_NAME || '')),
      W.el('nav', {}, ...(C.STATUS_URL ? [W.el('a', { href: C.STATUS_URL, 'data-i': 'status' })] : []), ...(C.DISCORD_URL ? [W.el('a', { href: C.DISCORD_URL, rel: 'noopener', 'data-i': 'discord' })] : []))));
    document.body.append(ftr);
    document.getElementById('lang-btn').onclick = () => { W.lang = W.lang === 'en' ? 'pl' : 'en'; try { localStorage.setItem('wos.lang', W.lang); } catch (e) { /* ignore */ } W.apply(); };
    const m = document.querySelector('main'); if (m) m.id = 'main';
  }
  W.init = async function (strings) {
    W.strings = strings || {}; chrome(); W.apply();
    try { W.cfg = await W.api('/public/config'); } catch (e) { W.cfg = {}; }
    if (W.cfg.known_issues === false) document.getElementById('nav-known').hidden = true;
    if (W.cfg.changelog === false) document.getElementById('nav-fixed').hidden = true;
    return W.cfg;
  };
})();
