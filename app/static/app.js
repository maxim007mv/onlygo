// OnlyGo UI — vanilla JS, no build step. All clicks go through one delegated handler (data-act).
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const uid = localStorage.og_uid || (localStorage.og_uid = 'u' + Math.random().toString(36).slice(2, 10));
let sid = localStorage.og_sid || null;
let last = null;
let checkoutOpen = false;
const rub = (x) => Math.round(x).toLocaleString('ru-RU') + ' ₽';
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const dm = (iso) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;
const tm = (iso) => iso.slice(11, 16);
const hrs = (m) => `${Math.floor(m / 60)} ч${m % 60 ? ' ' + (m % 60) + ' мин' : ''}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const btn = (act, label, data = {}, cls = 'btn primary sm') =>
  `<button type="button" class="${cls}" data-act="${act}" ${Object.entries(data).map(([k, v]) => `data-${k}="${esc(v)}"`).join(' ')}>${label}</button>`;


// ---------------- icons (inline SVG, Lucide-style strokes; own drawings)
const P = {
  plane: '<path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"/>',
  train: '<rect x="5" y="3" width="14" height="14" rx="3"/><path d="M5 11h14M9 21l1.5-4M15 21l-1.5-4M9 14h.01M15 14h.01"/>',
  bed: '<path d="M2 20V6M2 10h18a2 2 0 0 1 2 2v8M2 16h20"/><circle cx="7" cy="13" r="0"/><path d="M6 10V8.5A1.5 1.5 0 0 1 7.5 7h3A1.5 1.5 0 0 1 12 8.5V10"/>',
  car: '<path d="M5 17H3v-5l2-5h14l2 5v5h-2"/><path d="M3 12h18"/><circle cx="7.5" cy="17" r="2"/><circle cx="16.5" cy="17" r="2"/>',
  pin: '<path d="M20 10c0 5-8 12-8 12S4 15 4 10a8 8 0 0 1 16 0z"/><circle cx="12" cy="10" r="3"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  wallet: '<path d="M19 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0 0 4h15a1 1 0 0 1 1 1v4h-3a2 2 0 0 0 0 4h3a1 1 0 0 0 1-1v-2"/><path d="M3 5v14a2 2 0 0 0 2 2h15a1 1 0 0 0 1-1v-4"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  checkc: '<circle cx="12" cy="12" r="10"/><path d="m8 12 3 3 5-6"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  x: '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6M9 9l6 6"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  send: '<path d="M22 2 11 13M22 2l-7 20-4-9-9-4z"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  play: '<path d="m6 4 14 8-14 8z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  wave: '<path d="M2 12c2-2 4-2 6 0s4 2 6 0 4-2 6 0M2 18c2-2 4-2 6 0s4 2 6 0 4-2 6 0M2 6c2-2 4-2 6 0s4 2 6 0 4-2 6 0"/>',
  mountain: '<path d="m8 3 4 8 5-5 5 15H2z"/>',
  building: '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M9 22v-4h6v4M8 6h.01M16 6h.01M12 6h.01M12 10h.01M12 14h.01M16 10h.01M16 14h.01M8 10h.01M8 14h.01"/>',
  tree: '<path d="M12 22v-5M7 17h10l-3-4h2l-4-6-4 6h2z"/>',
  food: '<path d="M3 2v7c0 1.1.9 2 2 2h2a2 2 0 0 0 2-2V2M6 2v20M21 15V2a5 5 0 0 0-5 5v6c0 1.1.9 2 2 2h3zm0 0v7"/>',
  paw: '<circle cx="11" cy="4" r="2"/><circle cx="18" cy="8" r="2"/><circle cx="20" cy="16" r="2"/><path d="M9 10a5 5 0 0 1 5 5v3.5a3.5 3.5 0 0 1-6.84 1.05Q6.52 17.48 4.46 16.84A3.5 3.5 0 0 1 5.5 10z"/>',
  thermo: '<path d="M14 4v10.5a4 4 0 1 1-4 0V4a2 2 0 0 1 4 0z"/>',
  rain: '<path d="M20 16.6A5 5 0 0 0 18 7h-1.3A8 8 0 1 0 4 15.3M16 14v6M8 14v6M12 16v6"/>',
  clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
  card: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M2 10h20"/>',
  lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  ticket: '<path d="M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2z"/><path d="M13 5v2M13 17v2M13 11v2"/>',
  bulb: '<path d="M9 18h6M10 22h4M12 2a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2z"/>',
  memory: '<path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/>',
  edit: '<path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  help: '<circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01"/>',
  wrench: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9z"/>',
  arrow: '<path d="M5 12h14M12 5l7 7-7 7"/>',
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  map: '<path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3z"/><path d="M9 3v15M15 6v15"/>',
  suitcase: '<rect x="3" y="7" width="18" height="14" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M8 11v6M16 11v6"/>',
  star: '<path d="m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z"/>',
};
const ic = (n, cls = '') => `<svg class="ic ${cls}" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${P[n] || ''}</svg>`;
const hydrate = (root = document) => root.querySelectorAll('i.ic[data-ic]').forEach((el) => { el.outerHTML = ic(el.dataset.ic); });

async function api(path, body, method = 'POST') {
  const r = await fetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : (j.detail?.message || 'Что-то пошло не так, попробуйте ещё раз'));
  return j;
}
function add(html, cls = 'agent') {
  $('#empty').classList.add('hidden');
  const d = document.createElement('div'); d.className = 'msg ' + cls; d.innerHTML = html; $('#chat').appendChild(d);
  d.scrollIntoView({ behavior: 'smooth', block: 'start' }); return d;
}
const typing = () => add('<span class="typing"><span></span><span></span><span></span></span> <span class="muted">Ищу направления, отели и билеты, проверяю стыковки…</span>');

// ---------------- steps & hints
function setStep(r) {
  const p = r?.plan, b = r?.booking;
  const cur = b ? 6 : checkoutOpen ? 5 : p ? 4 : r?.candidates?.destinations?.length || r?.questions?.length ? 2 : 1;
  $$('#steps li').forEach((li) => { const n = +li.dataset.s; li.className = n < cur ? 'done' : n === cur ? 'cur' : ''; li.querySelector('b').innerHTML = n < cur ? ic('check') : n; li.toggleAttribute('aria-current', n === cur); });
}
function setHint(r) {
  const h = $('#hint');
  let t = '';
  if (r?.booking) t = ic('checkc') + ' Поездка оплачена. Ниже в плане — ранний заезд, перенос дат и напоминания.';
  else if (r?.plan?.connection_errors) t = ic('alert') + ' В плане есть ошибка стыковки — нажмите синюю кнопку «исправить» в плане справа.';
  else if (r?.plan) t = ic('info') + ' Нажмите «Выбрать» на карточке, чтобы заменить вариант. Можно изменить даты или бюджет кнопками ниже. Всё устраивает — «Перейти к оплате» в плане.';
  else if (r?.questions?.length) t = ic('help') + ' Ответьте на вопрос: нажмите вариант или напишите свой.';
  h.innerHTML = t; h.classList.toggle('hidden', !t);
}

// ---------------- actions
async function send(text) {
  text = (text || '').trim(); if (!text) return null;
  add(esc(text), 'user'); $('#msg').value = '';
  const wait = typing();
  try {
    const r = await api('/api/chat', { message: text, session_id: sid, user_id: uid });
    wait.remove(); sid = r.session_id; localStorage.og_sid = sid; render(r); return r;
  } catch (e) { wait.innerHTML = ic('alert') + ' ' + esc(e.message); return null; }
}
async function choose(kind, id) {
  try { const r = await api('/api/select', { session_id: sid, kind, id }); render(r); return r; } catch (e) { add(ic('alert') + ' ' + esc(e.message)); }
}
async function removeReq(slot) {
  try { const r = await api('/api/correct', { session_id: sid, slot }); render(r); } catch (e) { add(ic('alert') + ' ' + esc(e.message)); }
}
async function applyFix(i) {
  const f = last?.plan?.checks?.[i]?.fix; if (!f) return;
  if (f.say) return send(f.say);
  add(ic('wrench') + ' ' + esc(f.label), 'user');
  return choose(f.kind, f.id);
}
async function post(bid, action, value) {
  try { const r = await api('/api/post_purchase', { booking_id: bid, action, value: value || null }); add(ic('checkc') + ' ' + esc(r.message)); return r; } catch (e) { add(ic('alert') + ' ' + esc(e.message)); }
}

// ---------------- cards
const PH = { beach: ['sun', 't-sea'], sea: ['wave', 't-sea'], ski: ['mountain', 't-mnt'], mountains: ['mountain', 't-mnt'],
  city: ['building', 't-city'], food: ['food', 't-warm'], nature: ['tree', 't-mnt'] };
function ph(tags, fallback, rank, isSel) {
  const k = (tags || []).find((t) => PH[t]); const [n, tone] = k ? PH[k] : [fallback, 't-city'];
  return `<div class="ph"><span class="tile ${tone}">${ic(n)}</span>${isSel ? `<span class="tagsel">${ic('check')}в плане</span>` : `<span class="rank">#${rank + 1}</span>`}</div>`;
}
function li(cls, label, arr) { return arr?.length ? `<div class="lbl">${label}</div><ul class="${cls}">${arr.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''; }
function more(o, extra = '') {
  const body = li('why', 'Почему подходит', o.why) + li('trade', 'Компромиссы', o.tradeoffs) + li('risk', 'Риски', o.risks) + extra;
  return body ? `<details class="more"><summary>Почему этот вариант</summary>${body}</details>` : '';
}
const why1 = (o) => (o.why?.[0] ? `<div class="why1">${ic('check')}<span>${esc(o.why[0])}</span></div>` : '');
const a = (t, cls = '', n = '') => `<span class="${cls}">${n ? ic(n) : ''}${esc(t)}</span>`;
const pick = (kind, id, isSel, label) => (isSel ? '<button class="btn outline sm" disabled>' + ic('check') + 'Выбрано</button>' : btn('choose', label, { kind, id }));

function destCard(d, i, selId, budget) {
  const w = d.weather, s = d.id === selId, share = budget ? (d.est_total / budget) * 100 : null;
  return `<article class="card ${s ? 'sel' : ''}">${ph(d.tags, 'pin', i, s)}<div class="cb"><h4>${esc(d.name)}</h4><div class="city">${esc(d.country)}</div>
  <div class="attrs">${a('~' + d.flight_hours + ' ч', '', 'plane')}${a(Math.round(w.air) + '°', '', 'thermo')}${w.sea != null ? a('море ' + Math.round(w.sea) + '°', w.sea >= 24 ? 'good' : '', 'wave') : ''}${w.rainy ? a('дожди', 'bad', 'rain') : ''}${a(d.visa_free ? 'без визы' : 'виза', d.visa_free ? 'good' : 'bad')}</div>
  ${why1(d)}<div class="price"><small>от</small> ${rub(d.est_total)} <small>за всех</small></div>
  ${share != null ? `<div class="bar" title="${Math.round(share)}% бюджета"><i class="${share > 100 ? 'over' : ''}" style="width:${Math.min(100, share)}%"></i></div><small class="muted">${Math.round(share)}% бюджета</small>` : ''}
  ${more(d, d.factors?.length ? `<div class="lbl">Учитывал</div><div>${esc(d.factors.join(', '))}</div>` : '')}${pick('destination', d.id, s, 'Выбрать направление')}</div></article>`;
}
function hotelCard(h, i, selId) {
  const s = h.id === selId;
  return `<article class="card ${s ? 'sel' : ''}">${ph(h.distance_to_beach_m != null && h.distance_to_beach_m < 600 ? ['beach'] : ['city'], 'bed', i, s)}<div class="cb">
  <div><span class="stars" aria-label="${h.stars} звезды">${ic('star', 'fill').repeat(h.stars)}</span></div><h4>${esc(h.name)}</h4>
  <div><span class="score">${h.rating}</span> <small class="muted">${h.reviews} отзывов</small></div>
  <div class="attrs">${a(h.meals_ru)}${a(h.free_cancellation ? 'бесплатная отмена' : 'невозвратный', h.free_cancellation ? 'good' : 'bad')}${h.distance_to_beach_m != null ? a(h.distance_to_beach_m + ' м до пляжа', h.distance_to_beach_m <= 300 ? 'good' : '', 'pin') : ''}${h.kids_club ? a('детклуб', 'good') : ''}${h.pets_allowed ? a('с животными', 'good', 'paw') : ''}</div>
  ${why1(h)}<div class="price">${rub(h.total)} <small>${rub(h.price_per_night_calc)} / ночь</small></div>${more(h)}${pick('hotel', h.id, s, 'Выбрать отель')}</div></article>`;
}
function trCard(t, i, kind, selId) {
  const s = t.id === selId;
  return `<article class="card ${s ? 'sel' : ''}"><div class="cb"><div class="top"><h4 class="ih"><span class="tile sm t-city">${ic(t.mode === 'train' ? 'train' : 'plane')}</span>${esc(t.carrier)}</h4>${s ? `<span class="tagsel">${ic('check')}в плане</span>` : ''}</div>
  <div class="times"><b>${tm(t.depart)}</b><span class="line"></span><b>${tm(t.arrive)}</b><small class="muted">${dm(t.depart)}</small></div>
  <div class="attrs">${a(hrs(t.duration_min))}${a(t.stops ? t.stops + ' пересадка' : 'прямой', t.stops ? 'bad' : 'good')}${a(t.baggage_included ? 'багаж включён' : 'без багажа', t.baggage_included ? 'good' : '')}${a(t.refundable ? 'возвратный' : 'невозвратный')}</div>
  <div class="price">${rub(t.total)}</div>${more(t)}${pick(kind, t.id, s, 'Выбрать')}</div></article>`;
}
function xferCard(t, i, kind, selId) {
  const s = t.id === selId;
  return `<article class="card ${s ? 'sel' : ''}"><div class="cb"><h4 class="ih"><span class="tile sm t-warm">${ic('car')}</span>${esc(t.title)}</h4><div class="attrs">${a('~' + t.minutes + ' мин', '', 'clock')}</div>${why1(t)}
  <div class="price">${rub(t.total)}</div>${more(t)}${pick(kind, t.id, s, 'Выбрать')}</div></article>`;
}
const section = (title, sub, cards) => `<div class="section-t">${title}<small>${sub}</small></div><div class="cards">${cards}</div>`;

function render(r) {
  last = r;
  setMode(r.mode);
  let html = `<div class="reply">${esc(r.reply)}</div>`;
  if (r.changes?.length) html += `<div class="changes"><b>Что изменилось</b><ul>${r.changes.map((c) => `<li>${esc(c)}</li>`).join('')}</ul></div>`;
  (r.memory || []).forEach((m) => (html += `<div class="note">${ic('memory')}${esc(m)}</div>`));
  (r.notes || []).forEach((m) => (html += `<div class="note">${ic('edit')}${esc(m)}</div>`));
  (r.implicit || []).forEach((m) => (html += `<div class="note">${ic('bulb')}${esc(m)}</div>`));
  (r.questions || []).forEach((q) => {
    html += `<div class="q"><b>${ic('help')}${esc(q.text)}</b>${q.blocking ? '' : ' <small class="muted">— необязательно, план уже собран</small>'}<div class="opts">${q.options.map((o) => btn('send', esc(o), { msg: o }, 'chip-btn')).join('')}</div></div>`;
  });
  const c = r.candidates || {}, s = r.selected || {}, budget = r.plan?.budget;
  if (c.destinations?.length && (r.plan || !r.changes?.length)) {
    html += section('Направления', 'лучшие под ваши условия', c.destinations.slice(0, 4).map((d, i) => destCard(d, i, s.destination, budget)).join(''));
    if (r.plan) {
      if (c.hotels) html += section('Отели', 'сравнение топ-3', c.hotels.map((h, i) => hotelCard(h, i, s.hotel)).join(''));
      if (c.outbound) html += section('Билеты туда', '', c.outbound.map((t, i) => trCard(t, i, 'outbound', s.outbound)).join(''));
      if (c.return) html += section('Билеты обратно', '', c.return.map((t, i) => trCard(t, i, 'return', s.return)).join(''));
      if (c.transfers_out) html += section('Трансфер до отеля', '', c.transfers_out.map((t, i) => xferCard(t, i, 'transfer_out', s.transfer_out)).join(''));
    }
  }
  add(html);
  $('#edits').classList.toggle('hidden', !r.plan);
  renderSide(r);
}
function setMode(m) { $('#mode').textContent = m === 'llm' ? 'LLM-режим' : 'Offline-режим'; $('#mode').className = 'pill-badge ' + (m || ''); }

const IC = { outbound: 'plane', transfer_out: 'car', hotel: 'bed', transfer_ret: 'car', return: 'plane' };
const ORDER = { error: 0, warn: 1, error_fixed: 2, ok: 3 };
const ICON = { ok: 'checkc', warn: 'alert', error: 'x', error_fixed: 'wrench' };
function renderSide(r) {
  setStep(r); setHint(r);
  $('#reqs').classList.toggle('muted', !r.requirements?.length);
  $('#reqs').innerHTML = (r.requirements || []).map((q) => `<span class="chip s-${esc(String(q.source).split(/[ :]/)[0])}">${esc(q.label)} <i>${esc(q.source)}</i><button type="button" data-act="remove" data-slot="${esc(q.slot)}" aria-label="Убрать условие: ${esc(q.label)}">×</button></span>`).join('') || 'Пока ничего';
  $('#trace').innerHTML = (r.trace || []).map((t) => `<div class="${esc(t.status)}">${ic(t.status === 'run' ? 'play' : 'check')}${esc(t.tool)}${t.note ? ' (' + esc(t.note) + ')' : ''} — ${t.status === 'run' ? esc(t.ms) + ' мс' : 'переиспользовано'}</div>`).join('') || '—';
  const st = r.stats || {};
  $('#stats').textContent = `ходов: ${st.turns ?? 0} · уточнений: ${st.clarifications ?? 0} · до первой рекомендации: ${st.first_rec_ms != null ? st.first_rec_ms + ' мс' : '—'} · ответ: ${r.latency_ms ?? '—'} мс`;
  const p = r.plan, el = $('#plan');
  if (!p) { el.innerHTML = `<h3>Ваша поездка</h3><div class="empty-plan"><span class="tile lg t-city">${ic('suitcase')}</span>Здесь появится план: билеты, отель, трансферы, проверка стыковок и итоговая цена.</div>`; $('#tabTotal').textContent = ''; return; }
  $('#tabTotal').textContent = '· ' + rub(p.total);
  const b = r.booking, n = { ok: 0, warn: 0, error: 0 };
  p.checks.forEach((c) => { const k = c.level === 'error_fixed' ? 'ok' : c.level; n[k] = (n[k] || 0) + 1; });
  const checkHtml = (c, i) => `<div class="check ${esc(c.level)}">${ic(ICON[c.level] || 'info')}<div><b>${esc(c.title)}:</b> ${esc(c.text)}${c.fix ? `<br>${btn('fix', esc(c.fix.label) + ic('arrow'), { i })}` : ''}</div></div>`;
  const share = p.budget ? (p.total / p.budget) * 100 : null;
  el.innerHTML = `<div class="plan-h"><div><h3>${esc(p.destination.name)}, ${esc(p.destination.country)}</h3><div class="d">${dm(p.start_date)} – ${dm(p.end_date)} · ${p.nights} ноч.</div></div></div>
   <div class="tl">${p.items.map((i) => `<div class="tl-i"><span class="tl-dot">${ic(IC[i.kind] || 'pin')}</span><div class="row"><div>${esc(i.title)}<br><small>${esc(i.sub)}</small></div><div class="pr">${rub(i.price)}</div></div></div>`).join('')}</div>
   <div class="section-t">Проверка стыковок</div>
   <div class="sumline"><span class="o">${ic('check')}${n.ok} в порядке</span>${n.warn ? `<span class="w">${ic('alert')}${n.warn} внимание</span>` : ''}${n.error ? `<span class="e">${ic('x')}${n.error} ошибка</span>` : ''}</div>
   ${p.checks.map((c, i) => [c, i]).sort((x, y) => ORDER[x[0].level] - ORDER[y[0].level]).map(([c, i]) => checkHtml(c, i)).join('')}
   <div class="section-t">Корзина</div><div class="cart">${p.items.map((i) => `<div class="item"><span>${esc(i.title.split(':')[0])}<small>${esc(i.title.split(':').slice(1).join(':'))}</small></span><b>${rub(i.price)}</b></div>`).join('')}</div>
   <div class="total"><span>Итого</span><span>${rub(p.total)}</span></div>
   ${share != null ? `<div class="bar"><i class="${share > 100 ? 'over' : ''}" style="width:${Math.min(100, share)}%"></i></div><small class="muted">${Math.round(share)}% от бюджета ${rub(p.budget)}</small>` : ''}
   ${b ? postPurchase(b) : `<button type="button" class="btn primary big" style="margin-top:12px" data-act="checkout" ${p.connection_errors ? 'disabled' : ''}>Перейти к оплате</button>
     <div class="cta-note">${p.connection_errors ? 'Сначала исправьте ошибку стыковки выше' : 'Следующий шаг: данные путешественника и оплата (симуляция)'}</div>`}`;
}

function postPurchase(b) {
  return `<div class="voucher">${ic('card')}<span>Оплачено (симуляция) · бронь <b>${esc(b.booking_id)}</b> · ${rub(b.amount)}</span></div>
  <div class="section-t">Управление поездкой</div><div class="acts">
  ${[['early_checkin', 'Ранний заезд', '', 'clock'], ['late_checkout', 'Поздний выезд', '', 'clock'], ['change_dates', 'Перенести +2 дня', '2', 'calendar'], ['reminder', 'Напоминания', '', 'bell'], ['add_transfer', 'Трансфер к рейсу', '', 'car']]
      .map(([act, l, v, n]) => btn('post', ic(n) + l, { bid: b.booking_id, action: act, value: v || '' }, 'btn outline sm')).join('')}</div>`;
}

// ---------------- checkout
const fmtCard = (v) => v.replace(/\D/g, '').slice(0, 16).replace(/(.{4})/g, '$1 ').trim();
function checkout() {
  const p = last?.plan; if (!p) return;
  checkoutOpen = true; setStep(last);
  $('#modalBox').innerHTML = `<div class="msteps"><span class="done">1. Поездка</span><span class="on">2. Данные и оплата</span><span>3. Готово</span></div>
   <h3 id="mTitle">${esc(p.destination.name)} · ${dm(p.start_date)}–${dm(p.end_date)}</h3>
   <div class="cart">${p.items.map((i) => `<div class="item"><span>${esc(i.title)}</span><b>${rub(i.price)}</b></div>`).join('')}</div>
   <div class="total"><span>К оплате</span><span>${rub(p.total)}</span></div>
   <label>Имя и фамилия латиницей, как в паспорте<input id="pName" value="IVAN PETROV" autocomplete="name"></label>
   <div class="grid2"><label>Email для ваучеров<input id="pEmail" type="email" value="ivan@example.com"></label><label>Телефон<input id="pPhone" value="+7 900 000-00-00"></label></div>
   <label>Номер карты<input id="pCard" inputmode="numeric" value="4242 4242 4242 4242"></label>
   <div class="grid2"><label>Срок<input value="12/29"></label><label>CVC<input value="123" type="password"></label></div>
   <div class="secure">${ic('lock')}<span>Это симуляция: деньги не списываются. Карта 4242… — успешная оплата, …0002 — отказ банка.</span></div>
   <div id="payErr" class="check error hidden" role="alert"></div>
   <button type="button" class="btn primary big" id="payBtn" data-act="pay">Оплатить ${rub(p.total)}</button>
   <button type="button" class="btn outline big" data-act="close">Вернуться к поездке</button>`;
  $('#modal').classList.remove('hidden');
  $('#pName').focus();
}
function closeModal() { $('#modal').classList.add('hidden'); checkoutOpen = false; setStep(last); }
async function pay() {
  const b = $('#payBtn'); b.disabled = true; b.textContent = 'Обрабатываем платёж…';
  await sleep(700);
  const fail = (m, label) => { $('#payErr').textContent = m; $('#payErr').classList.remove('hidden'); b.disabled = false; b.textContent = label; };
  try {
    const r = await api('/api/checkout', { session_id: sid, name: $('#pName').value, email: $('#pEmail').value, card: $('#pCard').value });
    if (r.status !== 'paid') return fail(r.reason, 'Попробовать снова');
    closeModal();
    add(`${ic('checkc')} <b>Поездка оплачена</b> (симуляция). Бронь <b>${esc(r.booking_id)}</b> на ${rub(r.amount)}.${(r.vouchers || []).map((v) => `<div class="voucher">${ic('ticket')}<span>${esc(v.item)} · <b>${esc(v.confirmation)}</b></span></div>`).join('')}<div class="note">${esc(r.note)} Дальше могу: ранний заезд, перенос дат, напоминания — кнопки в плане.</div>`);
    const s = await api('/api/session/' + sid, null, 'GET'); last = s; renderSide(s); return r;
  } catch (e) { fail(e.message, 'Оплатить'); }
}

// ---------------- demo scenario
const DEMO = [
  ['Один запрос', () => send('хочу куда-нибудь к тёплому морю в ноябре, недорого, с ребёнком, без долгих перелётов')],
  ['Уточнение', () => send('ребёнку 5 лет')],
  ['Пересборка: даты', () => send('на 2 дня позже')],
  ['Пересборка: собака', () => send('мы поедем с собакой')],
  ['Исправить стыковку', async () => { const i = (last?.plan?.checks || []).findIndex((c) => c.fix && !c.fix.say); if (i >= 0) await applyFix(i); else add('<span class="muted">Стыковки без замечаний — исправлять нечего.</span>'); }],
  ['Оплата', async () => { checkout(); await sleep(1200); await pay(); }],
  ['После покупки', async () => { if (last?.booking) await post(last.booking.booking_id, 'early_checkin'); }],
];
async function runDemo() {
  newTrip();
  const bar = $('#demoBar'); bar.classList.remove('hidden');
  bar.innerHTML = '<b>Демо:</b> ' + DEMO.map((d, i) => `<span class="st" id="ds${i}">${i + 1}. ${d[0]}</span>`).join('');
  $('#demoBtn').disabled = true;
  for (let i = 0; i < DEMO.length; i++) {
    $('#ds' + i).className = 'st on';
    await DEMO[i][1](); await sleep(1600);
    $('#ds' + i).className = 'st done';
  }
  $('#demoBtn').disabled = false;
  add(ic('checkc') + ' Демо-сценарий завершён: один запрос → 1 уточнение → 2 пересборки → исправление стыковки → оплата → пост-покупка.');
}
function newTrip() {
  sid = null; last = null; localStorage.removeItem('og_sid');
  $('#chat').innerHTML = ''; $('#empty').classList.remove('hidden');
  ['#demoBar', '#edits', '#hint'].forEach((s) => $(s).classList.add('hidden'));
  renderSide({}); $('#msg').focus();
}
function tab(t) { $$('#tabs button').forEach((x) => x.classList.toggle('on', x.dataset.t === t)); $('#main').className = 'm-' + t; window.scrollTo({ top: 0 }); }

// ---------------- single delegated click handler
const ACTIONS = {
  send: (d) => send(d.msg), choose: (d) => choose(d.kind, d.id), remove: (d) => removeReq(d.slot), fix: (d) => applyFix(+d.i),
  post: (d) => post(d.bid, d.action, d.value), checkout: () => checkout(), pay: () => pay(), close: () => closeModal(),
  demo: () => runDemo(), new: () => newTrip(), tab: (d) => tab(d.t),
};
document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-act]');
  if (!el || el.disabled) return;
  const fn = ACTIONS[el.dataset.act]; if (fn) { e.preventDefault(); fn(el.dataset); }
});
document.addEventListener('input', (e) => { if (e.target.id === 'pCard') e.target.value = fmtCard(e.target.value); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && checkoutOpen) closeModal(); });
$('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });
$('#form').onsubmit = (e) => { e.preventDefault(); send($('#msg').value); };

(async () => {
  hydrate(); renderSide({});
  try { const cfg = await api('/api/config', null, 'GET'); setMode(cfg.mode); if (cfg.mode === 'llm') $('#mode').textContent += ' · ' + cfg.model; } catch { /* offline UI still works */ }
  if (new URLSearchParams(location.search).has('demo')) return runDemo();
  if (sid) { try { const s = await api('/api/session/' + sid, null, 'GET'); if (s.plan) { add('<span class="muted">Восстановил вашу последнюю поездку.</span>'); render(s); } } catch { sid = null; localStorage.removeItem('og_sid'); } }
})();
