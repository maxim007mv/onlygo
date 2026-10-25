"""Agent orchestrator: understanding -> clarification -> inspiration -> search/compare
-> explanation -> assembly -> (simulated) purchase, plus incremental re-planning.

Every tool call is recorded in a trace (tool name, reused/recomputed, ms) that the UI
shows so the user can see the agent's logic."""
from __future__ import annotations

import copy
import time
from datetime import date, timedelta

from . import datastore as ds
from . import llm, parser, tools
from .tools import rub

DEST_KEYS = {"moods", "month", "max_travel_hours", "visa_free_only", "prefer_train", "budget", "price_sensitive",
             "pets", "children", "exclude_destinations", "origin", "change_destination", "destination", "country", "meals"}
HOTEL_KEYS = {"start_date", "nights", "budget", "price_sensitive", "pets", "children", "adults", "meals",
              "flexible_cancellation", "beach_close", "center_close", "change_hotel", "child_ages"}
TRANSPORT_KEYS = {"start_date", "nights", "origin", "adults", "children", "pets", "prefer_train", "direct_only",
                  "max_travel_hours", "budget", "price_sensitive", "child_ages"}
TRANSIENT = {"shift_days", "extend_nights", "budget_factor", "change_destination", "change_hotel", "dates_flexible", "implicit_notes"}

LABELS = {
    "origin": lambda v, r: f"Откуда: {ds.origin(v)['name']}",
    "destination": lambda v, r: f"Куда: {ds.destination(v)['name']}",
    "start_date": lambda v, r: f"Даты: с {date.fromisoformat(v):%d.%m.%Y}, {r.get('nights') or 7} ноч.",
    "country": lambda v, r: f"Страна: {v}",
    "budget": lambda v, r: f"Бюджет: до {rub(v)}",
    "price_sensitive": lambda v, r: "Недорого",
    "adults": lambda v, r: f"Взрослых: {v}",
    "children": lambda v, r: f"Детей: {v}" + (f" ({', '.join(map(str, r.get('child_ages') or []))} лет)" if r.get("child_ages") else ""),
    "pets": lambda v, r: "С питомцем",
    "max_travel_hours": lambda v, r: f"Дорога ≤ {v} ч",
    "direct_only": lambda v, r: "Только прямые",
    "visa_free_only": lambda v, r: "Без визы",
    "prefer_train": lambda v, r: "Предпочтительно поезд",
    "meals": lambda v, r: f"Питание: {tools.MEAL_RU[v]}",
    "flexible_cancellation": lambda v, r: "Бесплатная отмена",
    "beach_close": lambda v, r: "Близко к пляжу",
    "center_close": lambda v, r: "В центре",
    "moods": lambda v, r: "Настроение: " + ", ".join({"warm": "тепло", "sea": "море", "beach": "пляж", "mountains": "горы", "ski": "лыжи", "city": "город", "food": "гастро", "calm": "спокойно", "party": "тусовки", "romantic": "романтика", "nature": "природа", "luxury": "люкс", "diving": "дайвинг"}.get(m, m) for m in v),
}


KIND_RU = {"destination": "направление", "hotel": "отель", "outbound": "билет туда", "return": "билет обратно",
           "transfer_out": "трансфер из аэропорта", "transfer_ret": "трансфер в аэропорт"}


def new_state() -> dict:
    return {"req": {}, "sources": {}, "asked": [], "selected": {}, "candidates": {}, "plan": None, "turns": 0,
            "clarifications": 0, "first_rec_ms": None, "started": time.time(), "booking": None, "history": []}


class Trace:
    def __init__(self):
        self.items = []

    def run(self, tool: str, fn, *a, note: str = "", **kw):
        t0 = time.perf_counter()
        res = fn(*a, **kw)
        self.items.append({"tool": tool, "status": "run", "ms": round((time.perf_counter() - t0) * 1000, 1), "note": note})
        return res

    def reuse(self, tool: str, note: str = ""):
        self.items.append({"tool": tool, "status": "reused", "ms": 0, "note": note})


def requirements_view(req: dict, sources: dict) -> list[dict]:
    out = []
    for k, fn in LABELS.items():
        v = req.get(k)
        if v in (None, False, [], 0) and not (k == "children" and v == 0 and False):
            continue
        try:
            out.append({"slot": k, "label": fn(v, req), "source": sources.get(k, "явно")})
        except Exception:  # noqa: BLE001
            pass
    return out


# ------------------------------------------------------------------ understanding
def understand(state: dict, text: str, trace: Trace, today: date) -> dict:
    req = state["req"]
    delta = None
    if llm.enabled():
        delta = trace.run("parse_request", llm.parse, text, today.isoformat(), req, [d["id"] for d in ds.destinations()], note="LLM")
    rules = trace.run("parse_request", parser.parse, text, today, req, note="rules")
    if delta is None:
        delta = rules
    else:  # LLM is primary, rules fill gaps (robustness)
        for k, v in rules.items():
            delta.setdefault(k, v)
    return delta


def apply_delta(state: dict, delta: dict, today: date) -> tuple[set, list[str]]:
    req, src = state["req"], state["sources"]
    changed, notes = set(), []
    old_month = req.get("month")
    for k, v in delta.items():
        if k in TRANSIENT:
            continue
        if k == "exclude_destinations":
            req[k] = sorted(set(req.get(k, [])) | set(v)); changed.add(k); continue
        if k == "moods" and req.get("moods"):
            v = sorted(set(req["moods"]) | set(v))
        if req.get(k) != v:
            req[k] = v; src[k] = "явно"; changed.add(k)
    if "start_date" in delta and "month" not in delta:
        req["month"] = date.fromisoformat(req["start_date"]).month
    if delta.get("shift_days") and req.get("start_date"):
        nd = date.fromisoformat(req["start_date"]) + timedelta(days=int(delta["shift_days"]))
        req["start_date"], req["month"] = nd.isoformat(), nd.month
        changed.add("start_date"); notes.append(f"Сдвинул даты на {delta['shift_days']:+d} дн.: заезд {nd:%d.%m}")
    if delta.get("extend_nights"):
        req["nights"] = max(1, (req.get("nights") or 7) + int(delta["extend_nights"]))
        changed.add("nights"); notes.append(f"Длительность: {req['nights']} ноч.")
    if delta.get("budget_factor"):
        base = req.get("budget") or (state["plan"] or {}).get("total") or 150000
        req["budget"] = int(round(base * float(delta["budget_factor"]), -3))
        src["budget"] = "из правки"; changed.add("budget"); notes.append(f"Новый бюджет: до {rub(req['budget'])}")
    if delta.get("change_destination") and state["selected"].get("destination"):
        req.setdefault("exclude_destinations", []).append(state["selected"]["destination"])
        req.pop("destination", None); changed |= {"change_destination", "exclude_destinations"}
    if delta.get("change_hotel") and state["selected"].get("hotel"):
        state.setdefault("excluded_hotels", []).append(state["selected"]["hotel"]); changed.add("change_hotel")
    if req.get("month") != old_month:
        changed.add("month")
    if delta.get("dates_flexible"):
        state["dates_flexible"] = True
    if "destination" in delta:
        changed.add("destination")
    return changed, notes


def apply_implicit(state: dict, notes: list[str]):
    """Implicit requirements derived from context (shown to the user as 'неявно')."""
    req, src = state["req"], state["sources"]
    if req.get("children") and not req.get("adults"):
        req["adults"] = 2; src["adults"] = "предположение"
    if not req.get("origin"):
        req["origin"] = "moscow"; src["origin"] = "по умолчанию"
    if not req.get("nights"):
        req["nights"] = 7; src["nights"] = "по умолчанию"
    removed = state.get("removed", [])
    if req.get("children") and not req.get("flexible_cancellation") and "flexible_cancellation" not in removed:
        req["flexible_cancellation"] = True; src["flexible_cancellation"] = "неявно: с ребёнком планы меняются"
    if req.get("meals") == "AI" and not req.get("moods") and "moods" not in removed:
        req["moods"] = ["sea", "beach"]; src["moods"] = "неявно: «всё включено» обычно про пляж"
    if req.get("price_sensitive") and not req.get("budget"):
        src["price_sensitive"] = "явно («недорого»)"


def clarifying_questions(state: dict, today: date) -> list[dict]:
    req, asked = state["req"], state["asked"]
    qs = []
    if not req.get("start_date"):
        if "dates" not in asked and not state.get("dates_flexible"):
            qs.append({"id": "dates", "text": "Когда планируете поехать и примерно на сколько дней?", "blocking": True,
                       "options": ["В ноябре на неделю", "На новогодние праздники", "В ближайшие 2 недели, дней на 5", "Не важно, когда дешевле"]})
        else:
            d = today + timedelta(days=21)
            req["start_date"], req["month"] = d.isoformat(), d.month
            state["sources"]["start_date"] = "по умолчанию (через 3 недели)"
    if req.get("children") and not req.get("child_ages") and "child_ages" not in asked:
        qs.append({"id": "child_ages", "text": "Сколько лет ребёнку? От возраста зависит цена билета и выбор отеля (сейчас считаю 6 лет).", "blocking": False,
                   "options": ["До 2 лет", "3–6 лет", "7–12 лет"]})
    if not req.get("moods") and not req.get("destination") and "moods" not in asked:
        qs.append({"id": "moods", "text": "Какой отдых хочется? (пока подобрал по общему балансу цены и погоды)" if req.get("start_date") else "Какой отдых хочется?",
                   "blocking": not qs and not req.get("start_date") and not req.get("country") and not req.get("meals"), "options": ["Тёплое море", "Город и гастрономия", "Горы и природа", "Лыжи"]})
    qs = qs[:2]
    for q in qs:
        asked.append(q["id"])
    return qs


# ------------------------------------------------------------------ planning
def plan(state: dict, changed: set, trace: Trace, force_all: bool = False) -> list[str]:
    req, sel, cand = state["req"], state["selected"], state["candidates"]
    changes: list[str] = []
    old_plan = copy.deepcopy(state.get("plan"))
    first = not sel.get("destination")
    # 1) destination
    if force_all or first or changed & DEST_KEYS:
        dests = trace.run("suggest_destinations", tools.suggest_destinations, req, limit=4)
        cand["destinations"] = dests
        cur = sel.get("destination")
        if req.get("destination"):
            new = req["destination"]
        elif cur and cur in [d["id"] for d in dests[:3]] and "change_destination" not in changed and not (
                "budget" in changed and req.get("budget") and next(d for d in dests if d["id"] == cur)["est_total"] > req["budget"] * 1.1
                and dests[0]["id"] != cur):
            new = cur
        else:
            new = dests[0]["id"]
        if cur and new != cur:
            changes.append(f"Направление: {ds.destination(cur)['name']} → {ds.destination(new)['name']} (старое больше не лучшее под новые условия)")
        elif cur and changed & DEST_KEYS:
            changes.append(f"Направление {ds.destination(new)['name']} по-прежнему подходит — оставил")
        dest_changed = new != cur
        sel["destination"] = new
        if new not in [d["id"] for d in dests]:  # explicitly requested destination not in top list
            allr = tools.suggest_destinations({**req, "destination": new}, limit=1)
            cand["destinations"] = allr + dests[:3]
    else:
        trace.reuse("suggest_destinations", "условия направления не менялись")
        dest_changed = False
    did = sel["destination"]
    # 2) hotels
    if force_all or dest_changed or changed & HOTEL_KEYS or not cand.get("hotels"):
        hs = trace.run("search_hotels", tools.search_hotels, req, did, limit=20)
        hs = [h for h in hs if h["id"] not in state.get("excluded_hotels", [])] or hs
        if not hs and not req.get("destination"):
            for alt in cand.get("destinations", []):
                alt_hs = tools.search_hotels(req, alt["id"], limit=20)
                if alt_hs:
                    changes.append(f"В {ds.destination(did)['name']} нет подходящих отелей — переключился на {alt['name']}")
                    did, hs, dest_changed = alt["id"], alt_hs, True
                    sel["destination"] = did
                    break
        cur = sel.get("hotel")
        keep = cur and not dest_changed and "change_hotel" not in changed and cur in [h["id"] for h in hs[:3]] and not (
            "budget" in changed and hs[0]["id"] != cur)
        new = cur if keep else (hs[0]["id"] if hs else None)
        if not hs:
            changes.append("Нет отелей под эти условия — ослабьте ограничения")
        elif cur and keep:
            changes.append(f"Отель оставил тот же (цена пересчитана под новые условия)")
        elif cur and new != cur and not dest_changed:
            old = ds.hotel(cur)
            changes.append(f"Отель: {old['name']} → {ds.hotel(new)['name']}" + (" (старый не принимает животных)" if req.get("pets") and not old["pets_allowed"] else " (лучше под новые условия)"))
        cand["hotels"] = hs[:3] + ([h for h in hs if h["id"] == new and h not in hs[:3]])
        sel["hotel"] = new
        hotel_changed = True
    else:
        trace.reuse("search_hotels", "условия по отелю не менялись")
        hotel_changed = False
    # 3) transport
    if force_all or dest_changed or changed & TRANSPORT_KEYS or not cand.get("outbound"):
        cand["outbound"] = trace.run("search_transport", tools.search_transport, req, did, "out", note="туда")
        cand["return"] = trace.run("search_transport", tools.search_transport, req, did, "ret", note="обратно")
        for k in ("outbound", "return"):
            if sel.get(k) not in [o["id"] for o in cand[k]]:
                sel[k] = cand[k][0]["id"] if cand[k] else None
        transport_changed = True
        if old_plan and not dest_changed and changed & {"start_date", "nights"}:
            changes.append("Билеты перепоиск на новые даты")
    else:
        trace.reuse("search_transport", "даты/пассажиры не менялись")
        transport_changed = False
    if not sel.get("hotel") or not sel.get("outbound") or not sel.get("return"):
        state["plan"] = None
        return changes
    out_t = next(o for o in cand["outbound"] if o["id"] == sel["outbound"])
    ret_t = next(o for o in cand["return"] if o["id"] == sel["return"])
    # 4) transfers
    if hotel_changed or transport_changed or not cand.get("transfers_out"):
        cand["transfers_out"] = trace.run("search_transfers", tools.search_transfers, req, did, sel["hotel"], out_t["arrive"], note="из аэропорта")
        cand["transfers_ret"] = trace.run("search_transfers", tools.search_transfers, req, did, sel["hotel"], ret_t["depart"], note="в аэропорт")
        for k, ck in (("transfer_out", "transfers_out"), ("transfer_ret", "transfers_ret")):
            if sel.get(k) not in [t["id"] for t in cand[ck]] or hotel_changed:
                sel[k] = cand[ck][0]["id"]
    else:
        trace.reuse("search_transfers")
    hotel = next(h for h in cand["hotels"] if h["id"] == sel["hotel"])
    tr_out = next(t for t in cand["transfers_out"] if t["id"] == sel["transfer_out"])
    tr_ret = next(t for t in cand["transfers_ret"] if t["id"] == sel["transfer_ret"])
    dest = next((d for d in cand["destinations"] if d["id"] == did), None) or tools.suggest_destinations({**req, "destination": did}, limit=1)[0]
    new_plan = trace.run("assemble_trip", tools.assemble_trip, req, dest, hotel, out_t, ret_t, tr_out, tr_ret)
    if old_plan:
        diff = new_plan["total"] - old_plan["total"]
        if diff:
            changes.append(f"Итог: {rub(old_plan['total'])} → {rub(new_plan['total'])} ({'+' if diff > 0 else '−'}{rub(abs(diff))})")
    state["plan"] = new_plan
    return changes


def compose_reply(state: dict, notes: list[str], changes: list[str], memory_notes: list[str]) -> str:
    p, req = state["plan"], state["req"]
    if not p:
        return "Не смог собрать поездку под все условия. Попробуйте ослабить ограничения (например, бюджет или длительность дороги)."
    d = p["destination"]
    h = next(i for i in p["items"] if i["kind"] == "hotel")
    o = next(i for i in p["items"] if i["kind"] == "outbound")
    summary = {"destination": d["name"], "why_destination": d["why"], "tradeoffs": d["tradeoffs"], "risks": d["risks"],
               "hotel": h["title"], "transport": o["title"], "total_rub": p["total"], "budget": req.get("budget"),
               "checks": [c["text"] for c in p["checks"] if c["level"] != "ok"], "changes": changes}
    text = None
    if llm.enabled():
        text = llm.explain(summary)
    if not text:
        parts = []
        if changes:
            parts.append("Пересобрал план, а не начинал заново.")
        else:
            parts.append(f"Предлагаю {d['name']} ({d['country']}).")
        if d["why"]:
            parts.append("Почему: " + "; ".join(d["why"][:3]) + ".")
        if d["tradeoffs"]:
            parts.append("Компромисс: " + "; ".join(d["tradeoffs"][:2]) + ".")
        parts.append(f"Собрал пакет: {o['title'].split(': ')[0].lower()}, {h['title']}, трансферы — итого {rub(p['total'])}.")
        warn = [c for c in p["checks"] if c["level"] in ("warn", "error", "error_fixed")]
        if warn:
            parts.append("Обратите внимание: " + " ".join(c["text"] for c in warn[:2]))
        parts.append("Можно выбрать другой вариант в карточках или написать правку («на 2 дня позже», «дешевле», «с собакой»).")
        text = " ".join(parts)
    return text


def handle_message(state: dict, text: str, profile: dict, today: date | None = None) -> dict:
    today = today or date.today()
    t0 = time.perf_counter()
    trace = Trace()
    state["turns"] += 1
    state["history"].append({"role": "user", "text": text})
    memory_notes = []
    if state["turns"] == 1 and profile:
        for k in ("origin", "pets", "meals"):
            if profile.get(k) and k not in state["req"]:
                state["req"][k] = profile[k]; state["sources"][k] = "из памяти"
        if profile.get("origin"):
            memory_notes.append(f"Помню, что вы обычно летите из города {ds.origin(profile['origin'])['name']}")
        if profile.get("pets"):
            memory_notes.append("Помню про питомца — ищу pet-friendly")
    # answers to quick options
    delta = understand(state, text, trace, today)
    changed, notes = apply_delta(state, delta, today)
    apply_implicit(state, notes)
    qs = clarifying_questions(state, today)
    blocking = [q for q in qs if q["blocking"]]
    if qs:
        state["clarifications"] += len(qs)
    resp = {"questions": qs, "notes": notes, "memory": memory_notes, "implicit": delta.get("implicit_notes", [])}
    if blocking:
        reply = "Понял запрос. " + ("Уже учёл: " + ", ".join(r["label"] for r in requirements_view(state["req"], state["sources"])) + ". " if state["req"] else "") + "Чтобы не гадать, уточню всего одно:"
        resp.update(reply=reply, plan=None, changes=[])
    else:
        changes = plan(state, changed, trace)
        if state["first_rec_ms"] is None and state["plan"]:
            state["first_rec_ms"] = round((time.time() - state["started"]) * 1000)
        reply = compose_reply(state, notes, changes, memory_notes)
        resp.update(reply=reply, plan=state["plan"], changes=changes)
    resp["trace"] = trace.items
    resp["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    state["history"].append({"role": "agent", "text": resp["reply"]})
    return finalize(state, resp)


def finalize(state: dict, resp: dict) -> dict:
    resp["requirements"] = requirements_view(state["req"], state["sources"])
    resp["candidates"] = state["candidates"] if state.get("plan") else {"destinations": state["candidates"].get("destinations", [])}
    resp["selected"] = state["selected"]
    resp["mode"] = "llm" if llm.enabled() else "offline"
    resp["stats"] = {"turns": state["turns"], "clarifications": state["clarifications"], "first_rec_ms": state["first_rec_ms"]}
    resp["booking"] = state.get("booking")
    return resp


def select(state: dict, kind: str, item_id: str) -> dict:
    trace = Trace()
    sel = state["selected"]
    changed = set()
    if kind == "destination":
        state["req"]["destination"] = item_id; state["sources"]["destination"] = "выбор пользователя"; changed.add("destination")
    elif kind in ("hotel", "outbound", "return", "transfer_out", "transfer_ret"):
        sel[kind] = item_id
        if kind == "hotel":
            state["candidates"]["transfers_out"] = []  # force transfer recompute
    changes = plan(state, changed, trace) if kind == "destination" else _reassemble(state, trace, kind)
    resp = {"reply": f"Готово, заменил {KIND_RU.get(kind, kind)} и пересобрал план со стыковками. " + " ".join(changes), "questions": [], "plan": state["plan"], "changes": changes, "trace": trace.items, "notes": [], "memory": []}
    return finalize(state, resp)


def _reassemble(state: dict, trace: Trace, kind: str) -> list[str]:
    old_total = (state.get("plan") or {}).get("total")
    if kind == "hotel":
        state["candidates"].pop("transfers_out", None)
    if kind in ("outbound", "return"):
        state["candidates"].pop("transfers_out", None)  # transfers depend on arrival time
    return plan(state, set(), trace)


def correct(state: dict, slot: str) -> dict:
    """User removes a requirement the agent inferred/was told (agent logic is editable)."""
    trace = Trace()
    req = state["req"]
    req.pop(slot, None)
    state.setdefault("removed", []).append(slot)
    state["sources"].pop(slot, None)
    if slot == "destination":
        state["selected"].pop("destination", None)
    apply_implicit(state, [])
    changes = plan(state, {slot}, trace) if req.get("start_date") else []
    resp = {"reply": f"Убрал условие «{slot}» и пересобрал план.", "questions": [], "plan": state["plan"], "changes": changes, "trace": trace.items, "notes": [], "memory": []}
    return finalize(state, resp)


