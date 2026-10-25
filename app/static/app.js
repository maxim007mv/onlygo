// OnlyGo chat UI — vanilla JS, no build step.
const $ = (s) => document.querySelector(s);
const uid = localStorage.og_uid || (localStorage.og_uid = 'u' + Math.random().toString(36).slice(2, 10));
let sid = localStorage.og_sid || null;
let last = null;
const rub = (x) => Math.round(x).toLocaleString('ru-RU') + ' ₽';
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmtT = (iso) => iso.replace('T', ' ').slice(5);
const hrs = (m) => `${Math.floor(m / 60)} ч ${m % 60 ? (m % 60) + ' мин' : ''}`;

async function api(path, body, method = 'POST') {
  const r = await fetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  const j = await r.json();
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : (j.detail?.message || 'Ошибка'));
  return j;
}
function add(html, cls = 'agent') { const d = document.createElement('div'); d.className = 'msg ' + cls; d.innerHTML = html; $('#chat').appendChild(d); $('#chat').scrollTop = 1e9; return d; }

async function send(text) {
  if (!text.trim()) return;
  add(esc(text), 'user'); $('#msg').value = '';
  const wait = add('<span class="muted">Думаю… (парсинг → поиск → сравнение → сборка)</span>');
  try {
    const r = await api('/api/chat', { message: text, session_id: sid, user_id: uid });
    wait.remove(); sid = r.session_id; localStorage.og_sid = sid; render(r);
  } catch (e) { wait.innerHTML = '⚠️ ' + esc(e.message); }
}
async function choose(kind, id) {
  try { const r = await api('/api/select', { session_id: sid, kind, id }); render(r); } catch (e) { add('⚠️ ' + esc(e.message)); }
}
async function removeReq(slot) {
  try { const r = await api('/api/correct', { session_id: sid, slot }); render(r); } catch (e) { add('⚠️ ' + esc(e.message)); }
}

function list(cls, label, arr) { return arr && arr.length ? `<div class="lbl">${label}</div><ul class="${cls}">${arr.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''; }
function explain(o) { return list('why', 'Почему подходит', o.why) + list('trade', 'Компромиссы', o.tradeoffs) + list('risk', 'Риски', o.risks); }

function destCard(d, selId) {
  const w = d.weather; return `<div class="card ${d.id === selId ? 'sel' : ''}"><h4>${esc(d.name)}</h4><div class="muted">${esc(d.country)} · ✈ ~${d.flight_hours} ч · ${d.visa_free ? 'без визы' : 'виза'}</div>
  <div>🌡 воздух ~${Math.round(w.air)}°C${w.sea != null ? ` · 🌊 море ~${Math.round(w.sea)}°C` : ''}${w.rainy ? ' · 🌧 дожди' : ''}</div>
  <div class="price">≈ ${rub(d.est_total)} за всех</div>${explain(d)}
  ${d.id === selId ? '<b>✓ выбрано</b>' : `<button onclick="choose('destination','${d.id}')">Выбрать</button>`}</div>`;
}
function hotelCard(h, selId) {
  return `<div class="card ${h.id === selId ? 'sel' : ''}"><h4>${esc(h.name)} ${'★'.repeat(h.stars)}</h4>
  <div class="muted">⭐ ${h.rating} · ${esc(h.meals_ru)} · ${h.free_cancellation ? 'бесплатная отмена' : 'невозвратный'}${h.distance_to_beach_m != null ? ' · пляж ' + h.distance_to_beach_m + ' м' : ''} · центр ${h.distance_to_center_km} км</div>
  <div class="price">${rub(h.total)} <small class="muted">(${rub(h.price_per_night_calc)}/ночь)</small></div>${explain(h)}
  ${h.id === selId ? '<b>✓ в плане</b>' : `<button onclick="choose('hotel','${h.id}')">Взять этот</button>`}</div>`;
}
function trCard(t, kind, selId) {
  return `<div class="card ${t.id === selId ? 'sel' : ''}"><h4>${t.mode === 'train' ? '🚆' : '✈'} ${esc(t.carrier)}</h4>
  <div>${fmtT(t.depart)} → ${fmtT(t.arrive)}</div><div class="muted">${hrs(t.duration_min)} · ${t.stops ? t.stops + ' пересадка' : 'прямой'} · ${t.baggage_included ? 'багаж вкл.' : 'без багажа'} · ${t.refundable ? 'возвратный' : 'невозвратный'}</div>
  <div class="price">${rub(t.total)}</div>${explain(t)}
  ${t.id === selId ? '<b>✓ в плане</b>' : `<button onclick="choose('${kind}','${t.id}')">Выбрать</button>`}</div>`;
}
function xferCard(t, kind, selId) {
  return `<div class="card ${t.id === selId ? 'sel' : ''}"><h4>🚕 ${esc(t.title)}</h4><div class="muted">~${t.minutes} мин</div><div class="price">${rub(t.total)}</div>
  ${list('why', 'Плюсы', t.why)}${list('trade', 'Минусы', t.tradeoffs)}
  ${t.id === selId ? '<b>✓ в плане</b>' : `<button onclick="choose('${kind}','${t.id}')">Выбрать</button>`}</div>`;
}

function render(r) {
  last = r;
  $('#mode').textContent = r.mode === 'llm' ? 'Режим: LLM' : 'Режим: offline demo (правила)';
  $('#mode').className = 'badge ' + r.mode;
  let html = esc(r.reply);
  (r.memory || []).forEach((m) => (html += `<div class="note">🧠 ${esc(m)}</div>`));
  (r.notes || []).forEach((m) => (html += `<div class="note">✎ ${esc(m)}</div>`));
  (r.implicit || []).forEach((m) => (html += `<div class="note">💡 ${esc(m)}</div>`));
  if (r.changes && r.changes.length) html += `<div class="changes"><b>Что изменилось:</b><ul>${r.changes.map((c) => `<li>${esc(c)}</li>`).join('')}</ul></div>`;
  (r.questions || []).forEach((q) => {
    html += `<div class="q">❓ ${esc(q.text)}${q.blocking ? '' : ' <small class="muted">(необязательно — план уже собран)</small>'}<div class="opts">${q.options.map((o) => `<button onclick="send(this.textContent)">${esc(o)}</button>`).join('')}</div></div>`;
  });
  const c = r.candidates || {}, s = r.selected || {};
  if (r.plan && c.destinations?.length) {
    html += `<div class="section-t">Направления</div><div class="cards">${c.destinations.slice(0, 4).map((d) => destCard(d, s.destination)).join('')}</div>`;
    if (c.hotels) html += `<div class="section-t">Отели — сравнение</div><div class="cards">${c.hotels.map((h) => hotelCard(h, s.hotel)).join('')}</div>`;
    if (c.outbound) html += `<div class="section-t">Туда</div><div class="cards">${c.outbound.map((t) => trCard(t, 'outbound', s.outbound)).join('')}</div>`;
    if (c.return) html += `<div class="section-t">Обратно</div><div class="cards">${c.return.map((t) => trCard(t, 'return', s.return)).join('')}</div>`;
    if (c.transfers_out) html += `<div class="section-t">Трансфер из аэропорта/вокзала</div><div class="cards">${c.transfers_out.map((t) => xferCard(t, 'transfer_out', s.transfer_out)).join('')}</div>`;
  }
  add(html);
  renderSide(r);
}

function renderSide(r) {
  $('#reqs').classList.toggle('muted', !r.requirements?.length);
  $('#reqs').innerHTML = (r.requirements || []).map((q) => `<span class="chip">${esc(q.label)} <i>${esc(q.source)}</i><button title="Убрать" onclick="removeReq('${q.slot}')">×</button></span>`).join('') || 'Пока ничего';
  $('#trace').innerHTML = (r.trace || []).map((t) => `<div class="${t.status}">${t.status === 'run' ? '▶' : '↺'} ${t.tool}${t.note ? ' (' + esc(t.note) + ')' : ''} — ${t.status === 'run' ? t.ms + ' мс' : 'переиспользовано'}</div>`).join('') || '—';
  const st = r.stats || {};
  $('#stats').textContent = `ходов: ${st.turns ?? 0} · уточнений: ${st.clarifications ?? 0} · до первой рекомендации: ${st.first_rec_ms != null ? (st.first_rec_ms / 1000).toFixed(1) + ' с' : '—'} · ответ: ${r.latency_ms ?? '—'} мс`;
  const p = r.plan, el = $('#plan');
  if (!p) { el.classList.add('hidden'); return; }
  el.classList.remove('hidden');
  const b = r.booking;
  el.innerHTML = `<h3>План поездки: ${esc(p.destination.name)} <small>${p.start_date.split('-').reverse().join('.')} – ${p.end_date.split('-').reverse().join('.')}, ${p.nights} ноч.</small></h3>
   ${p.items.map((i) => `<div class="item"><div>${esc(i.title)}<br><small>${esc(i.sub)}</small></div><div>${rub(i.price)}</div></div>`).join('')}
   <div class="total"><span>Итого</span><span>${rub(p.total)}</span></div>
   <div class="section-t">Проверка стыковок</div>${p.checks.map((c) => `<div class="check ${c.level}">${{ ok: '✅', warn: '⚠️', error: '❌', error_fixed: '🔧' }[c.level]} <b>${esc(c.title)}:</b> ${esc(c.text)}</div>`).join('')}
   ${b ? postPurchase(b) : `<button class="primary" style="margin-top:8px;width:100%" onclick="checkout()" ${p.connection_errors ? 'disabled' : ''}>Перейти к оплате (симуляция)</button>`}`;
}

function postPurchase(b) {
  return `<div class="check ok">💳 Оплачено (симуляция): бронь <b>${b.booking_id}</b>, ${rub(b.amount)}</div>
  <div class="section-t">После покупки</div><div class="chips">
  ${[['early_checkin', 'Ранний заезд'], ['late_checkout', 'Поздний выезд'], ['change_dates', 'Перенести на +2 дня', '2'], ['reminder', 'Напоминания'], ['add_transfer', 'Привязать трансфер к отелю']]
      .map(([a, l, v]) => `<button onclick="post('${b.booking_id}','${a}',${v ? `'${v}'` : 'null'})">${l}</button>`).join('')}</div>`;
}
async function post(bid, action, value) {
  try { const r = await api('/api/post_purchase', { booking_id: bid, action, value }); add('📌 ' + esc(r.message)); } catch (e) { add('⚠️ ' + esc(e.message)); }
}

function checkout() {
  const p = last.plan;
  $('#modalBox').innerHTML = `<h3>Оплата (симуляция)</h3><p class="muted">Деньги не списываются. Карта 4242… — успех, …0002 — отказ.</p>
   ${p.items.map((i) => `<div class="item"><small>${esc(i.title)}</small><span>${rub(i.price)}</span></div>`).join('')}
   <div class="total"><span>К оплате</span><span>${rub(p.total)}</span></div>
   <label>Имя</label><input id="pName" value="Иван Петров"><label>Email</label><input id="pEmail" value="ivan@example.com">
   <label>Карта</label><input id="pCard" value="4242 4242 4242 4242">
   <div id="payErr" class="check error hidden"></div>
   <button class="primary" onclick="pay()">Оплатить ${rub(p.total)}</button> <button onclick="$('#modal').classList.add('hidden')">Отмена</button>`;
  $('#modal').classList.remove('hidden');
}
async function pay() {
  try {
    const r = await api('/api/checkout', { session_id: sid, name: $('#pName').value, email: $('#pEmail').value, card: $('#pCard').value });
    if (r.status !== 'paid') { $('#payErr').textContent = r.reason; $('#payErr').classList.remove('hidden'); return; }
    $('#modal').classList.add('hidden');
    add(`✅ Оплата прошла (симуляция). Бронь <b>${r.booking_id}</b> на ${rub(r.amount)}.<br>${r.vouchers.map((v) => `• ${esc(v.item)} — ${v.confirmation}`).join('<br>')}<div class="note">${esc(r.note)}</div>`);
    const s = await api('/api/session/' + sid, null, 'GET'); renderSide(s);
  } catch (e) { $('#payErr').textContent = e.message; $('#payErr').classList.remove('hidden'); }
}

$('#form').onsubmit = (e) => { e.preventDefault(); send($('#msg').value); };
document.querySelectorAll('.ex,.ed').forEach((b) => (b.onclick = () => send(b.textContent)));
$('#newTrip').onclick = () => { sid = null; localStorage.removeItem('og_sid'); $('#chat').innerHTML = ''; greet(); renderSide({}); $('#plan').classList.add('hidden'); };
function greet() {
  add('Привет! Я OnlyGo — опишите поездку <b>одним сообщением</b>, как другу: куда тянет, когда, с кем, бюджет, что важно. Я сам пойму требования, уточню только необходимое, подберу направление, отели, билеты и трансферы, объясню выбор и соберу поездку в корзину.<div class="note">Все предложения построены на синтетических (mock) данных.</div>');
}
(async () => {
  const cfg = await api('/api/config', null, 'GET');
  $('#mode').textContent = cfg.mode === 'llm' ? 'Режим: LLM (' + cfg.model + ')' : 'Режим: offline demo (правила)';
  $('#mode').className = 'badge ' + cfg.mode;
  greet();
  if (sid) { try { const s = await api('/api/session/' + sid, null, 'GET'); if (s.plan) { add('<span class="muted">Восстановил вашу последнюю сессию.</span>'); render(s); } } catch { sid = null; localStorage.removeItem('og_sid'); } }
})();
