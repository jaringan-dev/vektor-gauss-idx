/* Runtime accessibility audit for the dashboard (WCAG 2.2 AA subset). Written here, no external library.
 *
 * Use: open any dashboard page (or the Today page), open the browser console, paste this whole file, then run
 *     await __runAll(['/', '/screener', '/stock/BBCA'])
 * Every page is fetched and rendered in a same-origin iframe (the pages send X-Frame-Options: DENY, so srcdoc is used), audited, and
 * the result is an object {url: {rule: {count, samples}}}. An empty object for a page means no findings.
 * Run it once with the OS in dark mode and once in light mode: contrast is checked on the computed colours.
 *
 * Rules: html-lang, page-title, zoom-disabled, main-landmark, nav-unlabelled, h1-count, heading-skip, duplicate-id, aria-ref-missing,
 *        form-label, control-name, placeholder-link, svg-name, img-alt, table-name, th-scope, target-size (24px), contrast (4.5 / 3).
 * Not covered (needs a person or a screen reader): reading order, focus order, meaning conveyed by colour alone, content of alt text.
 */
window.__audit = (d, w) => {
  const out = {};
  const add = (rule, el, note) => { (out[rule] = out[rule] || []).push((el && el.tagName ? el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (typeof el.className === 'string' && el.className ? '.' + el.className.split(' ')[0] : '') : '') + (note ? ' ' + note : '')); };
  const cs = e => w.getComputedStyle(e);
  const vis = e => { const r = e.getBoundingClientRect(); const s = cs(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const name = e => (e.getAttribute('aria-label') || (e.getAttribute('aria-labelledby') || '').split(' ').map(i => (d.getElementById(i) || {}).textContent || '').join(' ') || e.textContent || e.getAttribute('title') || e.alt || '').trim();
  if (!d.documentElement.lang) add('html-lang', d.documentElement);
  const vp = (d.querySelector('meta[name=viewport]') || {}).content || '';
  if (/user-scalable\s*=\s*no|maximum-scale\s*=\s*1(\.0)?\b/.test(vp)) add('zoom-disabled', null, vp);
  if (!d.title.trim()) add('page-title', null);
  const mains = d.querySelectorAll('main,[role=main]'); if (mains.length !== 1) add('main-landmark', null, String(mains.length));
  const navs = d.querySelectorAll('nav'); navs.forEach(n => { if (navs.length > 1 && !n.getAttribute('aria-label') && !n.getAttribute('aria-labelledby')) add('nav-unlabelled', n); });
  const hs = [...d.querySelectorAll('h1,h2,h3,h4,h5,h6')].filter(vis);
  const h1 = hs.filter(h => h.tagName === 'H1').length; if (h1 !== 1) add('h1-count', null, String(h1));
  let prev = 0; hs.forEach(h => { const l = +h.tagName[1]; if (prev && l > prev + 1) add('heading-skip', h, 'h' + prev + '->h' + l); prev = l; });
  const ids = {}; d.querySelectorAll('[id]').forEach(e => { ids[e.id] = (ids[e.id] || 0) + 1; }); Object.keys(ids).filter(k => ids[k] > 1).forEach(k => add('duplicate-id', null, k));
  d.querySelectorAll('[aria-labelledby],[aria-describedby],[aria-controls]').forEach(e => ['aria-labelledby', 'aria-describedby', 'aria-controls'].forEach(a => { (e.getAttribute(a) || '').split(/\s+/).filter(Boolean).forEach(i => { if (!d.getElementById(i)) add('aria-ref-missing', e, a + '=' + i); }); }));
  d.querySelectorAll('input:not([type=hidden]),select,textarea').forEach(e => { if (!vis(e)) return; const lab = e.id && d.querySelector('label[for="' + e.id + '"]'); if (!(lab || e.closest('label') || e.getAttribute('aria-label') || e.getAttribute('aria-labelledby') || e.title)) add('form-label', e); });
  d.querySelectorAll('a[href],button,[role=button]').forEach(e => { if (!vis(e)) return; if (!name(e)) add('control-name', e); if (e.tagName === 'A' && e.getAttribute('href') === '#') add('placeholder-link', e, name(e)); });
  d.querySelectorAll('svg[role=img]').forEach(e => { if (!name(e) && !e.querySelector('title')) add('svg-name', e); });
  d.querySelectorAll('img').forEach(e => { if (!e.hasAttribute('alt')) add('img-alt', e); });
  d.querySelectorAll('table').forEach(t => { if (!vis(t)) return; if (!t.querySelector('caption') && !t.getAttribute('aria-label') && !t.getAttribute('aria-labelledby')) add('table-name', t); t.querySelectorAll('th').forEach(th => { if (!th.getAttribute('scope') && !th.getAttribute('role')) add('th-scope', th); }); });
  d.querySelectorAll('a[href],button,input,select').forEach(e => { if (!vis(e)) return; if (e.closest('.sr-only')) return; const r = e.getBoundingClientRect(); const s = cs(e); if ((r.width < 24 || r.height < 24) && s.display !== 'inline') add('target-size', e, Math.round(r.width) + 'x' + Math.round(r.height) + ' ' + name(e).slice(0, 18)); });
  const lum = c => { const a = c.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2]; };
  const parse = s => { const m = s.match(/rgba?\(([^)]+)\)/); if (!m) return null; const p = m[1].split(',').map(x => parseFloat(x)); return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 }; };
  const bodyBg = parse(cs(d.body).backgroundColor);
  const bgOf = e => { const layers = []; let el = e; while (el) { const c = parse(cs(el).backgroundColor); if (c && c.a > 0) { layers.push(c); if (c.a >= 1) break; } el = el.parentElement; } let cur = (bodyBg && bodyBg.a > 0) ? { r: bodyBg.r, g: bodyBg.g, b: bodyBg.b } : { r: 255, g: 255, b: 255 }; for (let i = layers.length - 1; i >= 0; i--) { const l = layers[i]; cur = { r: l.r * l.a + cur.r * (1 - l.a), g: l.g * l.a + cur.g * (1 - l.a), b: l.b * l.a + cur.b * (1 - l.a) }; } return cur; };
  let checked = 0; const seen = new Set(); const walker = d.createTreeWalker(d.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const n = walker.currentNode; if (!n.textContent.trim()) continue; const e = n.parentElement; if (!e || seen.has(e) || !vis(e)) continue; seen.add(e);
    if (e.closest('script,style,noscript') || e.closest('[aria-hidden=true]') || e.closest('.sr-only') || e.closest('[hidden]')) continue;
    if (++checked > 3000) break;
    const s = cs(e); const fg = parse(s.color); if (!fg) continue; let op = 1; let p = e; while (p) { op *= parseFloat(cs(p).opacity); p = p.parentElement; }
    const bg = bgOf(e); const k = fg.a * op; const f = { r: fg.r * k + bg.r * (1 - k), g: fg.g * k + bg.g * (1 - k), b: fg.b * k + bg.b * (1 - k) };
    const L1 = lum([f.r, f.g, f.b]), L2 = lum([bg.r, bg.g, bg.b]); const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    const size = parseFloat(s.fontSize); const bold = parseInt(s.fontWeight) >= 700; const large = size >= 24 || (size >= 18.66 && bold); const need = large ? 3 : 4.5;
    if (ratio < need) add('contrast', e, ratio.toFixed(2) + '<' + need + ' "' + n.textContent.trim().slice(0, 22) + '"');
  }
  const summary = {}; Object.keys(out).forEach(k => { summary[k] = { count: out[k].length, samples: [...new Set(out[k])].slice(0, 3) }; });
  return summary;
};
window.__runAll = async (urls) => {
  const results = {};
  for (const u of urls) {
    const html = await (await fetch(u, { credentials: 'same-origin' })).text();
    const f = document.createElement('iframe'); f.style.cssText = 'position:absolute;left:-10000px;top:0;width:1280px;height:900px;border:0';
    f.srcdoc = html; document.body.appendChild(f);
    await new Promise(r => { f.onload = r; });
    await new Promise(r => setTimeout(r, 500));
    try { results[u] = window.__audit(f.contentDocument, f.contentWindow); } catch (e) { results[u] = { error: String(e) }; }
    f.remove();
  }
  return results;
};
