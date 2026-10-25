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
  $$('#steps li').forEach((li) => { const n = +li.dataset.s; li.className = n < cur ? 'done' : n === cur ? 'cur' : ''; li.toggleAttribute('aria-current', n === cur); });
}
function setHint(r) {
  const h = $('#hint');
  let t = '';
  if (r?.booking) t = '✅ Поездка оплачена. Ниже в плане — ранний заезд, перенос дат и напоминания.';
  else if (r?.plan?.connection_errors) t = '⛔ В плане есть ошибка стыковки — нажмите синюю кнопку «исправить» в плане справа.';
  else if (r?.plan) t = '👉 Нажмите «Выбрать» на карточке, чтобы заменить вариант. Можно изменить даты или бюджет кнопками ниже. Всё устраивает — «Перейти к оплате» в плане.';
  else if (r?.questions?.length) t = '👉 Ответьте на вопрос: нажмите вариант или напишите свой.';
  h.textContent = t; h.classList.toggle('hidden', !t);
}

// ---------------- actions
async function send(text) {
  text = (text || '').trim(); if (!text) return null;
  add(esc(text), 'user'); $('#msg').value = '';
  const wait = typing();
  try {
    const r = await api('/api/chat', { message: text, session_id: sid, user_id: uid });
    wait.remove(); sid = r.session_id; localStorage.og_sid = sid; render(r); return r;
  } catch (e) { wait.innerHTML = '⚠️ ' + esc(e.message); return null; }
}
async function choose(kind, id) {
  try { const r = await api('/api/select', { session_id: sid, kind, id }); render(r); return r; } catch (e) { add('⚠️ ' + esc(e.message)); }
}
async function removeReq(slot) {
  try { const r = await api('/api/correct', { session_id: sid, slot }); render(r); } catch (e) { add('⚠️ ' + esc(e.message)); }
}
async function applyFix(i) {
  const f = last?.plan?.checks?.[i]?.fix; if (!f) return;
  if (f.say) return send(f.say);
  add('🔧 ' + esc(f.label), 'user');
  return choose(f.kind, f.id);
}
async function post(bid, action, value) {
  try { const r = await api('/api/post_purchase', { booking_id: bid, action, value: value || null }); add('📌 ' + esc(r.message)); return r; } catch (e) { add('⚠️ ' + esc(e.message)); }
}

// ---------------- cards
const PH = { beach: ['🏖', '#bfe8ff', '#e3fad5'], sea: ['🌊', '#bfe8ff', '#eaf2ff'], ski: ['⛷', '#e8f0ff', '#ffffff'], mountains: ['🏔', '#d9f2d0', '#f2ffea'],
  city: ['🏙', '#eaf2ff', '#f4f4f4'], food: ['🍽', '#fcecd9', '#fff6ea'], nature: ['🌲', '#d9f2d0', '#f2ffea'] };
function ph(tags, fallback, rank, isSel) {
  const k = (tags || []).find((t) => PH[t]); const [ic, a, b] = k ? PH[k] : [fallback, '#eaf2ff', '#f2ffea'];
  return `<div class="ph" style="background:linear-gradient(135deg,${a},${b})" aria-hidden="true">${ic}${isSel ? '<span class="tagsel">✓ в плане</span>' : `<span class="rank">#${rank + 1}</span>`}</div>`;
}
function li(cls, label, arr) { return arr?.length ? `<div class="lbl">${label}</div><ul class="${cls}">${arr.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''; }
function more(o, extra = '') {
  const body = li('why', 'Почему подходит', o.why) + li('trade', 'Компромиссы', o.tradeoffs) + li('risk', 'Риски', o.risks) + extra;
  return body ? `<details class="more"><summary>Почему этот вариант</summary>${body}</details>` : '';
}
const why1 = (o) => (o.why?.[0] ? `<div class="why1">✓ ${esc(o.why[0])}</div>` : '');
const a = (t, cls = '') => `<span class="${cls}">${esc(t)}</span>`;
const pick = (kind, id, isSel, label) => (isSel ? '<button class="btn outline sm" disabled>✓ Выбрано</button>' : btn('choose', label, { kind, id }));

function destCard(d, i, selId, budget) {
  const w = d.weather, s = d.id === selId, share = budget ? (d.est_total / budget) * 100 : null;
  return `<article class="card ${s ? 'sel' : ''}">${ph(d.tags, '🧭', i, s)}<div class="cb"><h4>${esc(d.name)}</h4><div class="city">${esc(d.country)}</div>
  <div class="attrs">${a('✈ ~' + d.flight_hours + ' ч')}${a('🌡 ' + Math.round(w.air) + '°')}${w.sea != null ? a('🌊 ' + Math.round(w.sea) + '°', w.sea >= 24 ? 'good' : '') : ''}${w.rainy ? a('🌧 дожди', 'bad') : ''}${a(d.visa_free ? 'без визы' : 'виза', d.visa_free ? 'good' : 'bad')}</div>
  ${why1(d)}<div class="price"><small>от</small> ${rub(d.est_total)} <small>за всех</small></div>
  ${share != null ? `<div class="bar" title="${Math.round(share)}% бюджета"><i class="${share > 100 ? 'over' : ''}" style="width:${Math.min(100, share)}%"></i></div><small class="muted">${Math.round(share)}% бюджета</small>` : ''}
  ${more(d, d.factors?.length ? `<div class="lbl">Учитывал</div><div>${esc(d.factors.join(', '))}</div>` : '')}${pick('destination', d.id, s, 'Выбрать направление')}</div></article>`;
}
function hotelCard(h, i, selId) {
  const s = h.id === selId;
  return `<article class="card ${s ? 'sel' : ''}">${ph(h.distance_to_beach_m != null && h.distance_to_beach_m < 600 ? ['beach'] : ['city'], '🏨', i, s)}<div class="cb">
  <div><span class="stars" aria-label="${h.stars} звезды">${'★'.repeat(h.stars)}</span></div><h4>${esc(h.name)}</h4>
  <div><span class="score">${h.rating}</span> <small class="muted">${h.reviews} отзывов</small></div>
  <div class="attrs">${a(h.meals_ru)}${a(h.free_cancellation ? 'бесплатная отмена' : 'невозвратный', h.free_cancellation ? 'good' : 'bad')}${h.distance_to_beach_m != null ? a('🏖 ' + h.distance_to_beach_m + ' м', h.distance_to_beach_m <= 300 ? 'good' : '') : ''}${h.kids_club ? a('детклуб', 'good') : ''}${h.pets_allowed ? a('🐾 с животными', 'good') : ''}</div>
  ${why1(h)}<div class="price">${rub(h.total)} <small>${rub(h.price_per_night_calc)} / ночь</small></div>${more(h)}${pick('hotel', h.id, s, 'Выбрать отель')}</div></article>`;
}
function trCard(t, i, kind, selId) {
  const s = t.id === selId;
  return `<article class="card ${s ? 'sel' : ''}"><div class="cb"><div class="top"><h4>${t.mode === 'train' ? '🚆' : '✈'} ${esc(t.carrier)}</h4>${s ? '<span class="tagsel score">✓ в плане</span>' : ''}</div>
  <div style="font-size:18px"><b>${tm(t.depart)}</b> → <b>${tm(t.arrive)}</b> <small class="muted">${dm(t.depart)}</small></div>
  <div class="attrs">${a(hrs(t.duration_min))}${a(t.stops ? t.stops + ' пересадка' : 'прямой', t.stops ? 'bad' : 'good')}${a(t.baggage_included ? 'багаж включён' : 'без багажа', t.baggage_included ? 'good' : '')}${a(t.refundable ? 'возвратный' : 'невозвратный')}</div>
  <div class="price">${rub(t.total)}</div>${more(t)}${pick(kind, t.id, s, 'Выбрать')}</div></article>`;
}
function xferCard(t, i, kind, selId) {
  const s = t.id === selId;
  return `<article class="card ${s ? 'sel' : ''}"><div class="cb"><h4>🚕 ${esc(t.title)}</h4><div class="attrs">${a('~' + t.minutes + ' мин')}</div>${why1(t)}
  <div class="price">${rub(t.total)}</div>${more(t)}${pick(kind, t.id, s, 'Выбрать')}</div></article>`;
}
const section = (title, sub, cards) => `<div class="section-t">${title}<small>${sub}</small></div><div class="cards">${cards}</div>`;

function render(r) {
  last = r;
  setMode(r.mode);
  let html = `<div class="reply">${esc(r.reply)}</div>`;
  if (r.changes?.length) html += `<div class="changes"><b>Что изменилось</b><ul>${r.changes.map((c) => `<li>${esc(c)}</li>`).join('')}</ul></div>`;
  (r.memory || []).forEach((m) => (html += `<div class="note">🧠 ${esc(m)}</div>`));
  (r.notes || []).forEach((m) => (html += `<div class="note">✎ ${esc(m)}</div>`));
  (r.implicit || []).forEach((m) => (html += `<div class="note">💡 ${esc(m)}</div>`));
  (r.questions || []).forEach((q) => {
    html += `<div class="q"><b>❓ ${esc(q.text)}</b>${q.blocking ? '' : ' <small class="muted">— необязательно, план уже собран</small>'}<div class="opts">${q.options.map((o) => btn('send', esc(o), { msg: o }, 'chip-btn')).join('')}</div></div>`;
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
function setMode(m) { $('#mode').textContent = m === 'llm' ? '🤖 LLM-режим' : '⚡ Offline-режим'; $('#mode').className = 'pill-badge ' + (m || ''); }

const IC = { outbound: '✈', transfer_out: '🚕', hotel: '🏨', transfer_ret: '🚕', return: '✈' };
const ORDER = { error: 0, warn: 1, error_fixed: 2, ok: 3 };
const ICON = { ok: '✅', warn: '⚠️', error: '⛔', error_fixed: '🔧' };
function renderSide(r) {
  setStep(r); setHint(r);
  $('#reqs').classList.toggle('muted', !r.requirements?.length);
  $('#reqs').innerHTML = (r.requirements || []).map((q) => `<span class="chip s-${esc(String(q.source).split(/[ :]/)[0])}">${esc(q.label)} <i>${esc(q.source)}</i><button type="button" data-act="remove" data-slot="${esc(q.slot)}" aria-label="Убрать условие: ${esc(q.label)}">×</button></span>`).join('') || 'Пока ничего';
  $('#trace').innerHTML = (r.trace || []).map((t) => `<div class="${esc(t.status)}">${t.status === 'run' ? '▶' : '↺'} ${esc(t.tool)}${t.note ? ' (' + esc(t.note) + ')' : ''} — ${t.status === 'run' ? esc(t.ms) + ' мс' : 'переиспользовано'}</div>`).join('') || '—';
  const st = r.stats || {};
  $('#stats').textContent = `ходов: ${st.turns ?? 0} · уточнений: ${st.clarifications ?? 0} · до первой рекомендации: ${st.first_rec_ms != null ? st.first_rec_ms + ' мс' : '—'} · ответ: ${r.latency_ms ?? '—'} мс`;
  const p = r.plan, el = $('#plan');
  if (!p) { el.innerHTML = '<h3>Ваша поездка</h3><div class="empty-plan"><i>🧳</i>Здесь появится план: билеты, отель, трансферы, проверка стыковок и итоговая цена.</div>'; $('#tabTotal').textContent = ''; return; }
  $('#tabTotal').textContent = '· ' + rub(p.total);
  const b = r.booking, n = { ok: 0, warn: 0, error: 0 };
  p.checks.forEach((c) => { const k = c.level === 'error_fixed' ? 'ok' : c.level; n[k] = (n[k] || 0) + 1; });
  const checkHtml = (c, i) => `<div class="check ${esc(c.level)}">${ICON[c.level] || ''} <b>${esc(c.title)}:</b> ${esc(c.text)}${c.fix ? `<br>${btn('fix', '→ ' + esc(c.fix.label), { i })}` : ''}</div>`;
  const share = p.budget ? (p.total / p.budget) * 100 : null;
  el.innerHTML = `<div class="plan-h"><div><h3>${esc(p.destination.name)}, ${esc(p.destination.country)}</h3><div class="d">${dm(p.start_date)} – ${dm(p.end_date)} · ${p.nights} ноч.</div></div></div>
   <div class="tl">${p.items.map((i) => `<div class="tl-i" data-ic="${IC[i.kind] || '•'}"><div class="row"><div>${esc(i.title)}<br><small>${esc(i.sub)}</small></div><div class="pr">${rub(i.price)}</div></div></div>`).join('')}</div>
   <div class="section-t">Проверка стыковок</div>
   <div class="sumline"><span class="o">✓ ${n.ok} в порядке</span>${n.warn ? `<span class="w">⚠ ${n.warn} внимание</span>` : ''}${n.error ? `<span class="e">⛔ ${n.error} ошибка</span>` : ''}</div>
   ${p.checks.map((c, i) => [c, i]).sort((x, y) => ORDER[x[0].level] - ORDER[y[0].level]).map(([c, i]) => checkHtml(c, i)).join('')}
   <div class="section-t">Корзина</div><div class="cart">${p.items.map((i) => `<div class="item"><span>${esc(i.title.split(':')[0])}<small>${esc(i.title.split(':').slice(1).join(':'))}</small></span><b>${rub(i.price)}</b></div>`).join('')}</div>
   <div class="total"><span>Итого</span><span>${rub(p.total)}</span></div>
   ${share != null ? `<div class="bar"><i class="${share > 100 ? 'over' : ''}" style="width:${Math.min(100, share)}%"></i></div><small class="muted">${Math.round(share)}% от бюджета ${rub(p.budget)}</small>` : ''}
   ${b ? postPurchase(b) : `<button type="button" class="btn primary big" style="margin-top:12px" data-act="checkout" ${p.connection_errors ? 'disabled' : ''}>Перейти к оплате</button>
     <div class="cta-note">${p.connection_errors ? 'Сначала исправьте ошибку стыковки выше' : 'Следующий шаг: данные путешественника и оплата (симуляция)'}</div>`}`;
}

function postPurchase(b) {
  return `<div class="voucher">💳 Оплачено (симуляция) · бронь <b>${esc(b.booking_id)}</b> · ${rub(b.amount)}</div>
  <div class="section-t">Управление поездкой</div><div class="acts">
  ${[['early_checkin', '🕙 Ранний заезд'], ['late_checkout', '🕓 Поздний выезд'], ['change_dates', '📅 Перенести +2 дня', '2'], ['reminder', '🔔 Напоминания'], ['add_transfer', '🚕 Трансфер к рейсу']]
      .map(([act, l, v]) => btn('post', l, { bid: b.booking_id, action: act, value: v || '' }, 'btn outline sm')).join('')}</div>`;
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
   <div class="secure">🔒 Это симуляция: деньги не списываются. Карта 4242… — успешная оплата, …0002 — отказ банка.</div>
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
    add(`✅ <b>Поездка оплачена</b> (симуляция). Бронь <b>${esc(r.booking_id)}</b> на ${rub(r.amount)}.${(r.vouchers || []).map((v) => `<div class="voucher">🎫 ${esc(v.item)} · <b>${esc(v.confirmation)}</b></div>`).join('')}<div class="note">${esc(r.note)} Дальше могу: ранний заезд, перенос дат, напоминания — кнопки в плане.</div>`);
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
  add('🎬 Демо-сценарий завершён: один запрос → 1 уточнение → 2 пересборки → исправление стыковки → оплата → пост-покупка.');
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
  renderSide({});
  try { const cfg = await api('/api/config', null, 'GET'); setMode(cfg.mode); if (cfg.mode === 'llm') $('#mode').textContent += ' · ' + cfg.model; } catch { /* offline UI still works */ }
  if (new URLSearchParams(location.search).has('demo')) return runDemo();
  if (sid) { try { const s = await api('/api/session/' + sid, null, 'GET'); if (s.plan) { add('<span class="muted">Восстановил вашу последнюю поездку.</span>'); render(s); } } catch { sid = null; localStorage.removeItem('og_sid'); } }
})();
