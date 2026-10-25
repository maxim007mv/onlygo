// OnlyGo chat UI — vanilla JS, no build step.
const $ = (s) => document.querySelector(s);
const uid = localStorage.og_uid || (localStorage.og_uid = 'u' + Math.random().toString(36).slice(2, 10));
let sid = localStorage.og_sid || null;
let last = null;
const rub = (x) => Math.round(x).toLocaleString('ru-RU') + ' ₽';
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const dm = (iso) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;
const tm = (iso) => iso.slice(11, 16);
const hrs = (m) => `${Math.floor(m / 60)} ч${m % 60 ? ' ' + (m % 60) + ' мин' : ''}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(path, body, method = 'POST') {
  const r = await fetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json();
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : (j.detail?.message || 'Ошибка'));
  return j;
}
function add(html, cls = 'agent') { const d = document.createElement('div'); d.className = 'msg ' + cls; d.innerHTML = html; $('#chat').appendChild(d); $('#chat').scrollTop = 1e9; return d; }
const typing = () => add('<span class="typing"><span></span><span></span><span></span></span> <span class="muted">понимаю запрос → ищу направления, отели, билеты → проверяю стыковки</span>');

async function send(text) {
  if (!text.trim()) return null;
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

function list(cls, label, arr) { return arr && arr.length ? `<div class="lbl">${label}</div><ul class="${cls}">${arr.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''; }
function explain(o) { return list('why', 'Почему подходит', o.why) + list('trade', 'Компромиссы', o.tradeoffs) + list('risk', 'Риски', o.risks); }
const attr = (t, cls = '') => `<span class="${cls}">${esc(t)}</span>`;
function head(title, i, isSel) { return `<div class="top"><h4>${title}</h4>${isSel ? '<span class="sel-tag">в плане</span>' : `<span class="rank">#${i + 1}</span>`}</div>`; }
function act(kind, id, isSel, label = 'Выбрать') { return `<div class="act">${isSel ? '<span class="muted">✓ выбрано</span>' : `<button onclick="choose('${kind}','${id}')">${label}</button>`}</div>`; }

function destCard(d, i, selId, budget) {
  const w = d.weather, s = d.id === selId;
  const share = budget ? Math.min(100, (d.est_total / budget) * 100) : null;
  return `<div class="card ${s ? 'sel' : ''}">${head(esc(d.name), i, s)}<div class="muted">${esc(d.country)}</div>
  <div class="attrs">${attr('✈ ~' + d.flight_hours + ' ч')}${attr('🌡 ' + Math.round(w.air) + '°C')}${w.sea != null ? attr('🌊 море ' + Math.round(w.sea) + '°C', w.sea >= 24 ? 'good' : '') : ''}${w.rainy ? attr('🌧 дожди', 'bad') : ''}${attr(d.visa_free ? 'без визы' : 'нужна виза', d.visa_free ? 'good' : 'bad')}</div>
  <div class="price">≈ ${rub(d.est_total)} <small>за всех, оценка</small></div>
  ${share != null ? `<div class="bar"><i class="${d.est_total > budget ? 'over' : ''}" style="width:${share}%"></i></div><small class="muted">${Math.round((d.est_total / budget) * 100)}% бюджета</small>` : ''}
  ${d.factors?.length ? `<div class="note">Учитывал: ${esc(d.factors.join(', '))}</div>` : ''}
  ${explain(d)}${act('destination', d.id, s, 'Выбрать направление')}</div>`;
}
function hotelCard(h, i, selId, req) {
  const s = h.id === selId;
  return `<div class="card ${s ? 'sel' : ''}">${head(esc(h.name), i, s)}<div><span class="stars">${'★'.repeat(h.stars)}</span> · <b>${h.rating}</b> <small class="muted">(${h.reviews} отзывов)</small></div>
  <div class="attrs">${attr(h.meals_ru)}${attr(h.free_cancellation ? 'бесплатная отмена' : 'невозвратный', h.free_cancellation ? 'good' : 'bad')}${h.distance_to_beach_m != null ? attr('🏖 ' + h.distance_to_beach_m + ' м', h.distance_to_beach_m <= 300 ? 'good' : '') : ''}${attr('центр ' + h.distance_to_center_km + ' км')}${h.kids_club ? attr('детклуб', 'good') : ''}${h.pets_allowed ? attr('🐾 можно с животными', 'good') : ''}</div>
  <div class="price">${rub(h.total)} <small>${rub(h.price_per_night_calc)} / ночь</small></div>${explain(h)}${act('hotel', h.id, s, 'Взять этот отель')}</div>`;
}
function trCard(t, i, kind, selId) {
  const s = t.id === selId;
  return `<div class="card ${s ? 'sel' : ''}">${head((t.mode === 'train' ? '🚆 ' : '✈ ') + esc(t.carrier), i, s)}
  <div style="font-size:15px"><b>${tm(t.depart)}</b> → <b>${tm(t.arrive)}</b> <small class="muted">${dm(t.depart)}</small></div>
  <div class="attrs">${attr(hrs(t.duration_min))}${attr(t.stops ? t.stops + ' пересадка' : 'прямой', t.stops ? 'bad' : 'good')}${attr(t.baggage_included ? 'багаж включён' : 'без багажа', t.baggage_included ? 'good' : '')}${attr(t.refundable ? 'возвратный' : 'невозвратный')}</div>
  <div class="price">${rub(t.total)}</div>${explain(t)}${act(kind, t.id, s)}</div>`;
}
function xferCard(t, i, kind, selId) {
  const s = t.id === selId;
  return `<div class="card ${s ? 'sel' : ''}">${head('🚕 ' + esc(t.title), i, s)}<div class="attrs">${attr('~' + t.minutes + ' мин')}</div><div class="price">${rub(t.total)}</div>
  ${list('why', 'Плюсы', t.why)}${list('trade', 'Минусы', t.tradeoffs)}${act(kind, t.id, s)}</div>`;
}
function section(title, sub, cards) { return `<div class="section-t">${title}<small>${sub}</small></div><div class="cards">${cards}</div>`; }

function render(r) {
  last = r;
  setMode(r.mode);
  let html = `<div class="reply">${esc(r.reply)}</div>`;
  if (r.changes?.length) html += `<div class="changes"><b>Что изменилось</b><ul>${r.changes.map((c) => `<li>${esc(c)}</li>`).join('')}</ul></div>`;
  (r.memory || []).forEach((m) => (html += `<div class="note">🧠 ${esc(m)}</div>`));
  (r.notes || []).forEach((m) => (html += `<div class="note">✎ ${esc(m)}</div>`));
  (r.implicit || []).forEach((m) => (html += `<div class="note">💡 ${esc(m)}</div>`));
  (r.questions || []).forEach((q) => {
    html += `<div class="q">❓ ${esc(q.text)}${q.blocking ? '' : ' <small class="muted">— необязательно, план уже собран</small>'}<div class="opts">${q.options.map((o) => `<button onclick="send(this.textContent)">${esc(o)}</button>`).join('')}</div></div>`;
  });
  const c = r.candidates || {}, s = r.selected || {}, req = r.plan?.budget;
  if (c.destinations?.length && (r.plan || !r.changes?.length)) {
    html += section('Направления', 'ранжированы под ваши условия', c.destinations.slice(0, 4).map((d, i) => destCard(d, i, s.destination, req)).join(''));
    if (r.plan) {
      if (c.hotels) html += section('Отели', 'сравнение топ-3', c.hotels.map((h, i) => hotelCard(h, i, s.hotel)).join(''));
      if (c.outbound) html += section('Туда', '', c.outbound.map((t, i) => trCard(t, i, 'outbound', s.outbound)).join(''));
      if (c.return) html += section('Обратно', '', c.return.map((t, i) => trCard(t, i, 'return', s.return)).join(''));
      if (c.transfers_out) html += section('Трансфер до отеля', '', c.transfers_out.map((t, i) => xferCard(t, i, 'transfer_out', s.transfer_out)).join(''));
    }
  }
  add(html);
  $('#edits').classList.toggle('hidden', !r.plan);
  renderSide(r);
}
function setMode(m) { $('#mode').textContent = m === 'llm' ? 'LLM-режим' : 'Offline demo · правила'; $('#mode').className = 'badge ' + m; }

const IC = { outbound: '✈', transfer_out: '🚕', hotel: '🏨', transfer_ret: '🚕', return: '✈' };
function renderSide(r) {
  $('#reqs').classList.toggle('muted', !r.requirements?.length);
  $('#reqs').innerHTML = (r.requirements || []).map((q) => `<span class="chip s-${esc(String(q.source).split(' ')[0])}">${esc(q.label)} <i>${esc(q.source)}</i><button title="Убрать условие" onclick="removeReq('${q.slot}')">×</button></span>`).join('') || 'Пока ничего';
  $('#trace').innerHTML = (r.trace || []).map((t) => `<div class="${t.status}">${t.status === 'run' ? '▶' : '↺'} ${t.tool}${t.note ? ' (' + esc(t.note) + ')' : ''} — ${t.status === 'run' ? t.ms + ' мс' : 'переиспользовано'}</div>`).join('') || '—';
  const st = r.stats || {};
  $('#stats').textContent = `ходов: ${st.turns ?? 0} · уточнений: ${st.clarifications ?? 0} · до первой рекомендации: ${st.first_rec_ms != null ? st.first_rec_ms + ' мс' : '—'} · ответ: ${r.latency_ms ?? '—'} мс`;
  const p = r.plan, el = $('#plan');
  if (!p) { el.innerHTML = '<h3>План поездки</h3><div class="empty">Здесь появится собранная поездка: таймлайн, проверка стыковок и корзина.</div>'; $('#tabTotal').textContent = ''; return; }
  $('#tabTotal').textContent = '· ' + rub(p.total);
  const b = r.booking, n = { ok: 0, warn: 0, error: 0 };
  p.checks.forEach((c) => (n[c.level === 'error_fixed' ? 'ok' : c.level] = (n[c.level === 'error_fixed' ? 'ok' : c.level] || 0) + 1));
  const checkHtml = (c, i) => `<div class="check ${c.level}">${{ ok: '✅', warn: '⚠️', error: '⛔', error_fixed: '🔧' }[c.level]} <b>${esc(c.title)}:</b> ${esc(c.text)}${c.fix ? `<br><button class="fix" onclick="applyFix(${i})">→ ${esc(c.fix.label)}</button>` : ''}</div>`;
  const budgetBar = p.budget ? `<div class="bar"><i class="${p.total > p.budget ? 'over' : ''}" style="width:${Math.min(100, (p.total / p.budget) * 100)}%"></i></div><small class="muted">${Math.round((p.total / p.budget) * 100)}% от бюджета ${rub(p.budget)}</small>` : '';
  el.innerHTML = `<div class="plan-h"><div><h3>${esc(p.destination.name)}, ${esc(p.destination.country)}</h3><div class="d">${dm(p.start_date)} – ${dm(p.end_date)} · ${p.nights} ноч.</div></div><span class="badge mock">mock</span></div>
   <div class="tl">${p.items.map((i) => `<div class="tl-i" data-ic="${IC[i.kind] || '•'}"><div class="row"><div>${esc(i.title)}<br><small>${esc(i.sub)}</small></div><div class="pr">${rub(i.price)}</div></div></div>`).join('')}</div>
   <div class="section-t">Проверка стыковок</div>
   <div class="sumline"><span class="o">✓ ${n.ok} ок</span>${n.warn ? `<span class="w">⚠ ${n.warn} внимание</span>` : ''}${n.error ? `<span class="e">⛔ ${n.error} ошибка</span>` : ''}</div>
   ${p.checks.map((c, i) => [c, i]).sort((a, b) => ({ error: 0, warn: 1, error_fixed: 2, ok: 3 }[a[0].level] - { error: 0, warn: 1, error_fixed: 2, ok: 3 }[b[0].level])).map(([c, i]) => checkHtml(c, i)).join('')}
   <div class="section-t">Корзина</div><div class="cart">${p.items.map((i) => `<div class="item"><span>${esc(i.title.split(':')[0])}<small>${esc(i.title.split(':').slice(1).join(':'))}</small></span><b>${rub(i.price)}</b></div>`).join('')}</div>
   <div class="total"><span>Итого</span><span>${rub(p.total)}</span></div>${budgetBar}
   ${b ? postPurchase(b) : `<button class="primary big" style="margin-top:10px" onclick="checkout()" ${p.connection_errors ? 'disabled title="Сначала исправьте ошибки стыковок"' : ''}>Забронировать и оплатить</button>`}`;
}

function postPurchase(b) {
  return `<div class="voucher">💳 Оплачено (симуляция) · бронь <b>${b.booking_id}</b> · ${rub(b.amount)}</div>
  <div class="section-t">Управление поездкой</div><div class="chips">
  ${[['early_checkin', '🕙 Ранний заезд'], ['late_checkout', '🕓 Поздний выезд'], ['change_dates', '📅 Перенести на +2 дня', '2'], ['reminder', '🔔 Напоминания'], ['add_transfer', '🚕 Привязать трансфер к рейсу']]
      .map(([a, l, v]) => `<button onclick="post('${b.booking_id}','${a}',${v ? `'${v}'` : 'null'})">${l}</button>`).join('')}</div>`;
}
async function post(bid, action, value) {
  try { const r = await api('/api/post_purchase', { booking_id: bid, action, value }); add('📌 ' + esc(r.message)); return r; } catch (e) { add('⚠️ ' + esc(e.message)); }
}

const fmtCard = (v) => v.replace(/\D/g, '').slice(0, 16).replace(/(.{4})/g, '$1 ').trim();
function checkout() {
  const p = last.plan;
  $('#modalBox').innerHTML = `<div class="steps"><span>1. Поездка</span><span class="on">2. Данные и оплата</span><span>3. Готово</span></div>
   <h3 style="margin:0">${esc(p.destination.name)} · ${dm(p.start_date)}–${dm(p.end_date)}</h3>
   <div class="cart">${p.items.map((i) => `<div class="item"><span>${esc(i.title)}</span><b>${rub(i.price)}</b></div>`).join('')}</div>
   <div class="total"><span>К оплате</span><span>${rub(p.total)}</span></div>
   <label>Имя и фамилия латиницей (как в паспорте)</label><input id="pName" value="IVAN PETROV">
   <div class="grid2"><div><label>Email для ваучеров</label><input id="pEmail" value="ivan@example.com"></div><div><label>Телефон</label><input id="pPhone" value="+7 900 000-00-00"></div></div>
   <label>Номер карты</label><input id="pCard" inputmode="numeric" value="4242 4242 4242 4242" oninput="this.value=fmtCard(this.value)">
   <div class="grid2"><div><label>Срок</label><input value="12/29"></div><div><label>CVC</label><input value="123" type="password"></div></div>
   <div class="secure">🔒 Симуляция: деньги не списываются. 4242… — успех, …0002 — отказ банка.</div>
   <div id="payErr" class="check error hidden"></div>
   <button class="primary big" id="payBtn" onclick="pay()">Оплатить ${rub(p.total)}</button>
   <button class="ghost big" onclick="$('#modal').classList.add('hidden')">Вернуться к поездке</button>`;
  $('#modal').classList.remove('hidden');
}
async function pay() {
  const btn = $('#payBtn'); btn.disabled = true; btn.textContent = 'Обрабатываем платёж…';
  await sleep(700);
  try {
    const r = await api('/api/checkout', { session_id: sid, name: $('#pName').value, email: $('#pEmail').value, card: $('#pCard').value });
    if (r.status !== 'paid') { $('#payErr').textContent = r.reason; $('#payErr').classList.remove('hidden'); btn.disabled = false; btn.textContent = 'Попробовать снова'; return; }
    $('#modal').classList.add('hidden');
    add(`✅ <b>Поездка оплачена</b> (симуляция). Бронь <b>${r.booking_id}</b> на ${rub(r.amount)}.${r.vouchers.map((v) => `<div class="voucher">🎫 ${esc(v.item)} · <b>${v.confirmation}</b></div>`).join('')}<div class="note">${esc(r.note)} Дальше могу: ранний заезд, перенос дат, напоминания — кнопки в плане.</div>`);
    const s = await api('/api/session/' + sid, null, 'GET'); last = s; renderSide(s); return r;
  } catch (e) { $('#payErr').textContent = e.message; $('#payErr').classList.remove('hidden'); btn.disabled = false; btn.textContent = 'Оплатить'; }
}

// ---------------- Demo scenario: one click walks the whole flow
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
  $('#newTrip').click();
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

$('#form').onsubmit = (e) => { e.preventDefault(); send($('#msg').value); };
document.querySelectorAll('.ex').forEach((b) => (b.onclick = () => send(b.dataset.m)));
document.querySelectorAll('.ed').forEach((b) => (b.onclick = () => send(b.textContent)));
$('#demoBtn').onclick = runDemo;
document.querySelectorAll('#tabs button').forEach((b) => (b.onclick = () => { document.querySelectorAll('#tabs button').forEach((x) => x.classList.toggle('on', x === b)); $('main').className = 'm-' + b.dataset.t; }));
$('main').className = 'm-chatCol';
$('#newTrip').onclick = () => { sid = null; last = null; localStorage.removeItem('og_sid'); $('#chat').innerHTML = ''; $('#demoBar').classList.add('hidden'); $('#edits').classList.add('hidden'); greet(); renderSide({}); };
function greet() {
  add('Привет! Я <b>OnlyGo</b>. Опишите поездку <b>одним сообщением</b>, как другу: куда тянет, когда, с кем, бюджет, что важно.<br>Я сам пойму явные и неявные требования, спрошу только то, без чего нельзя, подберу направление, отель, билеты и трансферы, объясню выбор, проверю стыковки и соберу всё в одну корзину.<div class="note">Все предложения построены на синтетических (mock) данных; оплата — симуляция. Нажмите «▶ Демо-сценарий», чтобы увидеть весь путь за минуту.</div>');
}
(async () => {
  const cfg = await api('/api/config', null, 'GET');
  setMode(cfg.mode); if (cfg.mode === 'llm') $('#mode').textContent += ' · ' + cfg.model;
  greet();
  if (new URLSearchParams(location.search).has('demo')) return runDemo();
  if (sid) { try { const s = await api('/api/session/' + sid, null, 'GET'); if (s.plan) { add('<span class="muted">Восстановил вашу последнюю поездку.</span>'); render(s); } } catch { sid = null; localStorage.removeItem('og_sid'); } }
})();
