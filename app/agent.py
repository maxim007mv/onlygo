"""Agent orchestrator: understanding -> clarification -> inspiration -> search/compare
-> explanation -> assembly -> (simulated) purchase, plus incremental re-planning.

Every tool call is recorded in a trace (tool name, reused/recomputed, ms) that the UI
shows so the user can see the agent's logic."""

from __future__ import annotations

import copy
import logging
import time
from datetime import date, timedelta

from . import datastore as ds
from . import llm, parser, tools, trip
from .tools import DEFAULT_NIGHTS, rub, today_local

log = logging.getLogger("onlygo.agent")

DEST_KEYS = {
    "moods",
    "month",
    "max_travel_hours",
    "visa_free_only",
    "prefer_train",
    "budget",
    "price_sensitive",
    "pets",
    "children",
    "exclude_destinations",
    "origin",
    "change_destination",
    "destination",
    "country",
    "meals",
}
HOTEL_KEYS = {
    "start_date",
    "nights",
    "budget",
    "price_sensitive",
    "pets",
    "children",
    "adults",
    "meals",
    "flexible_cancellation",
    "beach_close",
    "center_close",
    "change_hotel",
    "child_ages",
}
TRANSPORT_KEYS = {
    "start_date",
    "nights",
    "origin",
    "adults",
    "children",
    "pets",
    "prefer_train",
    "direct_only",
    "max_travel_hours",
    "budget",
    "price_sensitive",
    "child_ages",
}
TRANSIENT = {
    "shift_days",
    "extend_nights",
    "budget_factor",
    "change_destination",
    "change_hotel",
    "dates_flexible",
    "implicit_notes",
    "unknown_place",
}

LABELS = {
    "origin": lambda v, r: f"Откуда: {ds.origin(v)['name']}",
    "destination": lambda v, r: f"Куда: {ds.destination(v)['name']}",
    "start_date": lambda v, r: f"Даты: с {date.fromisoformat(v):%d.%m.%Y}, {r.get('nights') or DEFAULT_NIGHTS} ноч.",
    "country": lambda v, r: f"Страна: {v}",
    "budget": lambda v, r: f"Бюджет: до {rub(v)}",
    "price_sensitive": lambda v, r: "Недорого",
    "adults": lambda v, r: f"Взрослых: {v}",
    "children": lambda v, r: f"Детей: {v}" + (f" ({', '.join(map(str, r.get('child_ages') or []))} лет)" if r.get("child_ages") else ""),
    "pets": lambda v, r: "С питомцем",
    "max_travel_hours": lambda v, r: f"Дорога ≤ {v:g} ч",
    "direct_only": lambda v, r: "Только прямые",
    "accessibility": lambda v, r: "Доступная среда",
    "elderly": lambda v, r: "Пожилой путешественник",
    "multi_city": lambda v, r: "Несколько городов",
    "visa_free_only": lambda v, r: "Без визы",
    "prefer_train": lambda v, r: "Предпочтительно поезд",
    "meals": lambda v, r: f"Питание: {tools.MEAL_RU[v]}",
    "flexible_cancellation": lambda v, r: "Бесплатная отмена",
    "beach_close": lambda v, r: "Близко к пляжу",
    "center_close": lambda v, r: "В центре",
    "moods": lambda v, r: "Настроение: " + ", ".join(MOOD_RU.get(m, m) for m in v),
}
MOOD_RU = {
    "warm": "тепло",
    "sea": "море",
    "beach": "пляж",
    "mountains": "горы",
    "ski": "лыжи",
    "city": "город",
    "food": "гастро",
    "calm": "спокойно",
    "party": "тусовки",
    "romantic": "романтика",
    "nature": "природа",
    "luxury": "люкс",
    "diving": "дайвинг",
}


KIND_RU = {
    "destination": "направление",
    "hotel": "отель",
    "outbound": "билет туда",
    "return": "билет обратно",
    "transfer_out": "трансфер из аэропорта",
    "transfer_ret": "трансфер в аэропорт",
}


def new_state() -> dict:
    return {
        "req": {},
        "sources": {},
        "asked": [],
        "selected": {},
        "candidates": {},
        "plan": None,
        "turns": 0,
        "clarifications": 0,
        "first_rec_ms": None,
        "started": time.time(),
        "booking": None,
        "history": [],
    }


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


DEFAULT_BUDGET_BASE = 150_000  # base for «дешевле/дороже» when no budget and no plan yet
DEFAULT_LEAD_DAYS = 21  # «не важно когда» → через 3 недели
NEAREST_LEAD_DAYS = 7  # place/duration known, dates not: «ближайшие» = через неделю
BUDGET_TOLERANCE = 1.05
MAX_HISTORY = 40  # turns kept in session state (bounded growth)
SELECTABLE = {
    "destination": "destinations",
    "hotel": "hotels",
    "outbound": "outbound",
    "return": "return",
    "transfer_out": "transfers_out",
    "transfer_ret": "transfers_ret",
}


class AgentInputError(ValueError):
    """Bad user action (unknown option, nothing to change) — mapped to HTTP 400, never 500."""


def requirements_view(req: dict, sources: dict) -> list[dict]:
    """Chips «что я понял» with the source of each slot (явно / неявно / по умолчанию / из памяти)."""
    out = []
    for k, fn in LABELS.items():
        v = req.get(k)
        if v in (None, False, [], 0):
            continue
        try:
            out.append({"slot": k, "label": fn(v, req), "source": sources.get(k, "явно")})
        except (KeyError, TypeError, ValueError):  # stale slot from an old session/data version: skip, but leave a trace
            log.warning("cannot render requirement %s=%r", k, v)
    return out


# ------------------------------------------------------------------ understanding
def understand(state: dict, text: str, trace: Trace, today: date) -> dict:
    req = state["req"]
    delta = None
    if llm.enabled():
        delta = trace.run("parse_request", llm.parse, text, today.isoformat(), req, [d["id"] for d in ds.destinations()], note="LLM")
    rules = trace.run("parse_request", parser.parse, text, today, req, note="rules")
    if not isinstance(delta, dict):
        delta = rules
    else:  # LLM is primary, rules fill gaps (robustness)
        for k, v in rules.items():
            delta.setdefault(k, v)
    return parser.sanitize(delta, today)


def apply_delta(state: dict, delta: dict, today: date) -> tuple[set, list[str]]:
    req, src = state["req"], state["sources"]
    changed, notes = set(), []
    old_month = req.get("month")
    for k, v in delta.items():
        if k in TRANSIENT:
            continue
        if k == "exclude_destinations":
            req[k] = sorted(set(req.get(k, [])) | set(v))
            changed.add(k)
            continue
        if k == "moods" and req.get("moods"):
            v = sorted(set(req["moods"]) | set(v))
        if req.get(k) != v:
            req[k] = v
            src[k] = "явно"
            changed.add(k)
    if "start_date" in delta and "month" not in delta:
        req["month"] = date.fromisoformat(req["start_date"]).month
    if delta.get("shift_days") and req.get("start_date"):
        nd = date.fromisoformat(req["start_date"]) + timedelta(days=int(delta["shift_days"]))
        req["start_date"], req["month"] = nd.isoformat(), nd.month
        changed.add("start_date")
        notes.append(f"Сдвинул даты на {delta['shift_days']:+d} дн.: заезд {nd:%d.%m}")
    if delta.get("extend_nights"):
        req["nights"] = min(parser.MAX_NIGHTS, max(1, (req.get("nights") or DEFAULT_NIGHTS) + int(delta["extend_nights"])))
        changed.add("nights")
        notes.append(f"Длительность: {req['nights']} ноч.")
    if delta.get("budget_factor"):
        base = req.get("budget") or (state["plan"] or {}).get("total") or DEFAULT_BUDGET_BASE
        req["budget"] = int(round(base * float(delta["budget_factor"]), -3))
        src["budget"] = "из правки"
        changed.add("budget")
        notes.append(f"Новый бюджет: до {rub(req['budget'])}")
    if delta.get("change_destination") and state["selected"].get("destination"):
        req.setdefault("exclude_destinations", []).append(state["selected"]["destination"])
        req.pop("destination", None)
        changed |= {"change_destination", "exclude_destinations"}
    if delta.get("change_hotel") and state["selected"].get("hotel"):
        state.setdefault("excluded_hotels", []).append(state["selected"]["hotel"])
        changed.add("change_hotel")
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
        req["adults"] = 2
        src["adults"] = "предположение"
    if not req.get("origin"):
        req["origin"] = "moscow"
        src["origin"] = "по умолчанию"
    if not req.get("nights"):
        req["nights"] = DEFAULT_NIGHTS
        src["nights"] = "по умолчанию"
    removed = state.get("removed", [])
    if req.get("children") and not req.get("flexible_cancellation") and "flexible_cancellation" not in removed:
        req["flexible_cancellation"] = True
        src["flexible_cancellation"] = "неявно: с ребёнком планы меняются"
    if req.get("meals") == "AI" and not req.get("moods") and "moods" not in removed:
        req["moods"] = ["sea", "beach"]
        src["moods"] = "неявно: «всё включено» обычно про пляж"
    if req.get("price_sensitive") and not req.get("budget"):
        src["price_sensitive"] = "явно («недорого»)"


DATE_OPTIONS = ["В ноябре на неделю", "На новогодние праздники", "В ближайшие 2 недели, дней на 5", "Не важно, когда дешевле"]


def _date_question(state: dict, today: date) -> dict | None:
    """Dates block only when we know too little to guess; with a known place or duration we take the nearest dates."""
    req, src = state["req"], state["sources"]
    concrete = req.get("destination") or req.get("country") or src.get("nights") == "явно"
    if "dates" not in state["asked"] and not state.get("dates_flexible") and not concrete:
        return {"id": "dates", "text": "Когда планируете поехать и примерно на сколько дней?", "blocking": True, "options": DATE_OPTIONS}
    lead = NEAREST_LEAD_DAYS if concrete else DEFAULT_LEAD_DAYS
    d = today + timedelta(days=lead)
    req["start_date"], req["month"] = d.isoformat(), d.month
    src["start_date"] = f"по умолчанию (через {lead} дн.)"
    if concrete and "dates" not in state["asked"]:
        return {
            "id": "dates",
            "text": f"Даты не назвали — взял ближайшие: с {d:%d.%m}. Если другие, напишите.",
            "blocking": False,
            "options": ["На 7 дней позже", "В декабре", "На новогодние праздники"],
        }
    return None


def clarifying_questions(state: dict, today: date) -> list[dict]:
    """At most two questions; only truly blocking ones stop planning."""
    req, asked = state["req"], state["asked"]
    qs = []
    if not req.get("start_date"):
        q = _date_question(state, today)
        if q:
            qs.append(q)
    if req.get("children") and not req.get("child_ages") and "child_ages" not in asked:
        who = "ребёнку" if req["children"] == 1 else "детям"
        qs.append(
            {
                "id": "child_ages",
                "text": f"Сколько лет {who}? От возраста зависит цена билета и выбор отеля (сейчас считаю 6 лет).",
                "blocking": False,
                "options": ["До 2 лет", "3–6 лет", "7–12 лет"],
            }
        )
    if not req.get("moods") and not req.get("destination") and "moods" not in asked:
        qs.append(
            {
                "id": "moods",
                "text": "Какой отдых хочется? (пока подобрал по общему балансу цены и погоды)"
                if req.get("start_date")
                else "Какой отдых хочется?",
                "blocking": not qs and not req.get("start_date") and not req.get("country") and not req.get("meals"),
                "options": ["Тёплое море", "Город и гастрономия", "Горы и природа", "Лыжи"],
            }
        )
    qs = qs[:2]
    for q in qs:
        asked.append(q["id"])
    return qs


# ------------------------------------------------------------------ planning
def plan(state: dict, changed: set, trace: Trace, force_all: bool = False) -> list[str]:
    """Incremental re-planning: recompute only the stages whose inputs changed, reuse the rest."""
    changes: list[str] = []
    state["unmet"] = None
    old_plan = copy.deepcopy(state.get("plan"))
    dest_changed = _plan_destination(state, changed, trace, changes, force_all)
    did_before = state["selected"]["destination"]
    hotel_changed = _plan_hotels(state, changed, trace, changes, force_all or dest_changed)
    dest_changed = dest_changed or state["selected"]["destination"] != did_before  # hotel fallback switched city
    transport_changed = _plan_transport(state, changed, trace, changes, force_all or dest_changed, bool(old_plan))
    sel = state["selected"]
    if not (sel.get("hotel") and sel.get("outbound") and sel.get("return")):
        state["plan"] = None
        return changes
    _plan_transfers(state, trace, hotel_changed, transport_changed)
    new_plan = _assemble(state, trace)
    if state.get("unmet"):
        u = state["unmet"]
        check = {"level": "warn", "title": "Условие не выполнено", "text": u["text"]}
        if u["alt"]:
            check["fix"] = {"label": f"Перейти в {u['alt']['name']}", "kind": "destination", "id": u["alt"]["id"]}
        new_plan["checks"].insert(0, check)
    if old_plan and new_plan["total"] != old_plan["total"]:
        diff = new_plan["total"] - old_plan["total"]
        changes.append(f"Итог: {rub(old_plan['total'])} → {rub(new_plan['total'])} ({'+' if diff > 0 else '−'}{rub(abs(diff))})")
    state["plan"] = new_plan
    return changes


def _keep_destination(state: dict, dests: list[dict], changed: set) -> bool:
    cur = state["selected"].get("destination")
    if not cur or cur not in [d["id"] for d in dests[:3]] or "change_destination" in changed:
        return False
    budget = state["req"].get("budget")
    cur_est = next(d for d in dests if d["id"] == cur)["est_total"]
    over_budget = "budget" in changed and budget and cur_est > budget * 1.1 and dests[0]["id"] != cur
    return not over_budget


def _plan_destination(state: dict, changed: set, trace: Trace, changes: list[str], force: bool) -> bool:
    req, sel, cand = state["req"], state["selected"], state["candidates"]
    cur = sel.get("destination")
    if not (force or not cur or changed & DEST_KEYS):
        trace.reuse("suggest_destinations", "условия направления не менялись")
        return False
    dests = trace.run("suggest_destinations", tools.suggest_destinations, req, limit=4)
    cand["destinations"] = dests
    if req.get("destination"):
        new = req["destination"]
    else:
        new = cur if _keep_destination(state, dests, changed) else dests[0]["id"]
    if cur and new != cur:
        changes.append(
            f"Направление: {ds.destination(cur)['name']} → {ds.destination(new)['name']} "
            + ("(ваш выбор)" if "destination" in changed and req.get("destination") else "(старое больше не лучшее под новые условия)")
        )
    elif cur and changed & DEST_KEYS:
        changes.append(f"Направление {ds.destination(new)['name']} по-прежнему подходит — оставил")
    sel["destination"] = new
    if new not in [d["id"] for d in dests]:  # explicitly requested destination not in top list
        cand["destinations"] = [tools.destination_card(req, new)] + dests[:3]
    return new != cur


def _fallback_destination(state: dict, changes: list[str]) -> list[dict]:
    """Current destination has no fitting hotels: switch to the next candidate that has some."""
    req, sel = state["req"], state["selected"]
    for alt in state["candidates"].get("destinations", []):
        alt_hs = tools.search_hotels(req, alt["id"], limit=20)
        if alt_hs:
            changes.append(f"В {ds.destination(sel['destination'])['name']} нет подходящих отелей — переключился на {alt['name']}")
            sel["destination"] = alt["id"]
            return alt_hs
    return []


def _hotel_alternatives(state: dict, limit: int = 3) -> list[dict]:
    """Destinations (other than the user's pick) that do have hotels for the current hard filters."""
    req = {**state["req"], "destination": None}
    cur = state["selected"]["destination"]
    dests = tools.suggest_destinations(req, limit=8)
    return [d for d in dests if d["id"] != cur and tools.search_hotels(req, d["id"], limit=1)][:limit]


def _keep_unmet_hotel(state: dict, changes: list[str]) -> None:
    """The user picked the destination themselves and it has no hotel for a hard filter (e.g. pets):
    don't throw the plan away and don't silently switch the city — keep it, say so, offer alternatives."""
    name = ds.destination(state["selected"]["destination"])["name"]
    alts = _hotel_alternatives(state)
    what = "отелей, которые принимают животных" if state["req"].get("pets") else "отелей под эти условия"
    alt_txt = f" Есть в: {', '.join(a['name'] for a in alts)} — переключиться можно кнопкой в плане." if alts else ""
    text = f"В {name} в демо-каталоге нет {what}. Отель оставил прежний, но условие он не выполняет.{alt_txt}"
    changes.append(text)
    state["unmet"] = {"text": text, "alt": alts[0] if alts else None}


def _plan_hotels(state: dict, changed: set, trace: Trace, changes: list[str], dest_changed: bool) -> bool:
    req, sel, cand = state["req"], state["selected"], state["candidates"]
    if not (dest_changed or changed & HOTEL_KEYS or not cand.get("hotels")):
        trace.reuse("search_hotels", "условия по отелю не менялись")
        return False
    hs = trace.run("search_hotels", tools.search_hotels, req, sel["destination"], limit=20)
    hs = [h for h in hs if h["id"] not in state.get("excluded_hotels", [])] or hs
    if not hs and not req.get("destination"):
        hs, dest_changed = _fallback_destination(state, changes), True
    cur = sel.get("hotel")
    if not hs and req.get("destination") and cur and cand.get("hotels") and not dest_changed:
        _keep_unmet_hotel(state, changes)
        return False
    keep = (
        bool(cur)
        and not dest_changed
        and "change_hotel" not in changed
        and cur in [h["id"] for h in hs[:3]]
        and not ("budget" in changed and hs[0]["id"] != cur)
    )
    new = cur if keep else (hs[0]["id"] if hs else None)
    if not hs:
        changes.append("Нет отелей под эти условия — ослабьте ограничения")
    elif keep:
        changes.append("Отель оставил тот же (цена пересчитана под новые условия)")
    elif cur and not dest_changed:
        old = ds.hotel(cur)
        reason = " (старый не принимает животных)" if req.get("pets") and not old["pets_allowed"] else " (лучше под новые условия)"
        changes.append(f"Отель: {old['name']} → {ds.hotel(new)['name']}{reason}")
    cand["hotels"] = hs[:3] + [h for h in hs if h["id"] == new and h not in hs[:3]]
    sel["hotel"] = new
    return True


def _plan_transport(state: dict, changed: set, trace: Trace, changes: list[str], dest_changed: bool, had_plan: bool) -> bool:
    req, sel, cand = state["req"], state["selected"], state["candidates"]
    if not (dest_changed or changed & TRANSPORT_KEYS or not cand.get("outbound")):
        trace.reuse("search_transport", "даты/пассажиры не менялись")
        return False
    did = sel["destination"]
    cand["outbound"] = trace.run("search_transport", tools.search_transport, req, did, "out", note="туда")
    cand["return"] = trace.run("search_transport", tools.search_transport, req, did, "ret", note="обратно")
    for k in ("outbound", "return"):
        if sel.get(k) not in [o["id"] for o in cand[k]]:
            sel[k] = cand[k][0]["id"] if cand[k] else None
    if "prefer_train" in changed and req.get("prefer_train") and not any(o.get("mode") == "train" for o in cand["outbound"]):
        changes.append(f"Поезда до {ds.destination(did)['name']} нет — оставил перелёт; поездом можно, например, в Сочи")
    if had_plan and not dest_changed and changed & {"start_date", "nights"}:
        changes.append("Билеты перепоискал на новые даты")
    return True


def _by_id(items: list[dict], item_id: str | None) -> dict | None:
    return next((o for o in items or [] if o["id"] == item_id), None)


def _plan_transfers(state: dict, trace: Trace, hotel_changed: bool, transport_changed: bool) -> None:
    req, sel, cand = state["req"], state["selected"], state["candidates"]
    if not (hotel_changed or transport_changed or not cand.get("transfers_out")):
        trace.reuse("search_transfers")
        return
    did, hid = sel["destination"], sel["hotel"]
    out_t, ret_t = _by_id(cand["outbound"], sel["outbound"]), _by_id(cand["return"], sel["return"])
    cand["transfers_out"] = trace.run("search_transfers", tools.search_transfers, req, did, hid, out_t["arrive"], note="из аэропорта")
    cand["transfers_ret"] = trace.run("search_transfers", tools.search_transfers, req, did, hid, ret_t["depart"], note="в аэропорт")
    for k, ck in (("transfer_out", "transfers_out"), ("transfer_ret", "transfers_ret")):
        if hotel_changed or not _by_id(cand[ck], sel.get(k)):
            sel[k] = cand[ck][0]["id"]


def _assemble(state: dict, trace: Trace) -> dict:
    req, sel, cand = state["req"], state["selected"], state["candidates"]
    did = sel["destination"]
    dest = _by_id(cand.get("destinations"), did) or tools.destination_card(req, did)
    return trace.run(
        "assemble_trip",
        trip.assemble_trip,
        req,
        dest,
        trip.Legs(
            hotel=_by_id(cand["hotels"], sel["hotel"]),
            outbound=_by_id(cand["outbound"], sel["outbound"]),
            ret=_by_id(cand["return"], sel["return"]),
            transfer_out=_by_id(cand["transfers_out"], sel["transfer_out"]),
            transfer_ret=_by_id(cand["transfers_ret"], sel["transfer_ret"]),
        ),
    )


def compose_reply(state: dict, notes: list[str], changes: list[str], memory_notes: list[str]) -> str:
    p, req = state["plan"], state["req"]
    if not p:
        return "Не смог собрать поездку под все условия. Попробуйте ослабить ограничения (например, бюджет или длительность дороги)."
    d = p["destination"]
    h = next(i for i in p["items"] if i["kind"] == "hotel")
    o = next(i for i in p["items"] if i["kind"] == "outbound")
    summary = {
        "destination": d["name"],
        "why_destination": d["why"],
        "tradeoffs": d["tradeoffs"],
        "risks": d["risks"],
        "hotel": h["title"],
        "transport": o["title"],
        "total_rub": p["total"],
        "budget": req.get("budget"),
        "checks": [c["text"] for c in p["checks"] if c["level"] != "ok"],
        "changes": changes,
    }
    text = None
    if llm.enabled():
        text = llm.explain(summary)
    if not text:
        parts = []
        o_item = next(i for i in p["items"] if i["kind"] == "outbound")
        if changes and state.get("rebuilt_all"):
            parts.append("Пересчитал все шаги заново (сменился месяц/сезон или направление — цены и погода другие); изменения ниже.")
        elif changes:
            parts.append("Пересобрал только затронутое — изменения ниже, остальное оставил.")
        else:
            parts.append(f"Предлагаю {d['name']} ({d['country']}), {p['nights']} ноч. с {p['start_date'][8:10]}.{p['start_date'][5:7]}.")
        dest_changed = any(c.startswith("Направление:") or "переключился" in c for c in changes)
        if d["why"] and (not changes or dest_changed):
            parts.append("Почему: " + "; ".join(d["why"][:4]) + ".")
        if d["tradeoffs"] and (not changes or dest_changed):
            parts.append("Компромисс: " + "; ".join(d["tradeoffs"][:2]) + ".")
        line = f"Пакет: {o_item['title'].split(': ')[0].lower()}, {h['title']}, трансферы — итого {rub(p['total'])}"
        if req.get("budget"):
            line += f" ({p['total'] * 100 / req['budget']:.0f}% бюджета {rub(req['budget'])})"
        parts.append(line + ".")
        warn = [c for c in p["checks"] if c["level"] in ("warn", "error", "error_fixed")]
        if warn:
            parts.append(
                f"Проверил стыковки: замечаний — {len(warn)} ("
                + ", ".join(c["title"].lower() for c in warn[:3])
                + "), подробности и кнопки исправления — в плане."
            )
        else:
            parts.append("Все стыковки проверены — конфликтов нет.")
        parts += honest_limits(state)
        parts.append("Можно выбрать другой вариант в карточках или написать правку («на 2 дня позже», «дешевле», «с собакой»).")
        text = " ".join(parts)
    return text


def honest_limits(state: dict) -> list[str]:
    """Say plainly when the request cannot be met (budget, travel time) instead of hiding it in a check."""
    p, req = state["plan"], state["req"]
    out = []
    budget = req.get("budget")
    if budget and p["total"] > budget * BUDGET_TOLERANCE:
        out.append(
            f"Честно: в {rub(budget)} под эти условия не уложиться — даже самый дешёвый подходящий вариант "
            f"дороже на {rub(p['total'] - budget)}. "
            "Можно сократить поездку («на 2 дня короче»), выбрать поезд или Россию."
        )
    maxh = req.get("max_travel_hours")
    hours = p["destination"].get("flight_hours") or 0
    if maxh and hours > maxh:
        out.append(
            f"Дорога ~{hours:.1f} ч — дольше вашего лимита {maxh:g} ч"
            + (": это направление вы выбрали сами." if req.get("destination") else ": ближе подходящих вариантов не нашлось.")
        )
    if req.get("accessibility"):
        out.append(
            "Доступная среда: в демо-каталоге нет данных о безбарьерных номерах — выбрал отель с коротким трансфером; "
            "перед оплатой подтвердим условия у отеля (в продакшене — фильтр по полю инвентаря Ostrovok)."
        )
    if req.get("multi_city"):
        out.append(
            "Маршрут из нескольких городов пока собираю как одну поездку — спланировал последний город; "
            "второй участок можно запросить отдельным сообщением."
        )
    if req.get("direct_only") and any(i.get("stops") for i in p["items"] if i["kind"] in ("outbound", "return")):
        out.append("Прямых рейсов под эти даты не нашлось — в плане рейс с пересадкой.")
    return out


def _load_memory(state: dict, profile: dict) -> list[str]:
    if state["turns"] != 1 or not profile:
        return []
    for k in ("origin", "pets", "meals"):
        if profile.get(k) and k not in state["req"]:
            state["req"][k] = profile[k]
            state["sources"][k] = "из памяти"
    notes = []
    if profile.get("origin"):
        notes.append(f"Помню, что вы обычно летите из города {ds.origin(profile['origin'])['name']}")
    if profile.get("pets"):
        notes.append("Помню про питомца — ищу pet-friendly")
    return notes


def _blocking_reply(state: dict, blocking: list[dict]) -> str:
    known = requirements_view(state["req"], state["sources"])
    head = "Понял запрос. " + (f"Уже учёл: {', '.join(r['label'] for r in known)}. " if known else "")
    return head + "Чтобы не гадать, " + _ask_phrase(len(blocking))


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Russian plural form: 1 вопрос, 2 вопроса, 5 вопросов (11–14 → many)."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _ask_phrase(n: int) -> str:
    if n == 1:
        return "уточню всего одно:"
    return f"уточню {n} {plural_ru(n, 'вопрос', 'вопроса', 'вопросов')}:"


def _unknown_place_note(state: dict, delta: dict, today: date) -> list[str]:
    place = delta.get("unknown_place")
    if not place or delta.get("destination"):
        return []
    n = len(ds.destinations())
    req = dict(state["req"])
    req.setdefault("month", today.month)
    close = [d["name"] for d in tools.suggest_destinations(req, limit=3)]
    return [
        f"{place} пока нет в демо-каталоге ({n} {plural_ru(n, 'направление', 'направления', 'направлений')}, данные синтетические). "
        f"Не подменяю молча: ближе всего по типу отдыха — {', '.join(close)}. "
        "Соберу план по лучшему из них, или назовите другой город."
    ]


def handle_message(state: dict, text: str, profile: dict, today: date | None = None) -> dict:
    """One user turn: understand → (maybe) ask → plan/re-plan → explain."""
    today = today or today_local()
    t0 = time.perf_counter()
    trace = Trace()
    state["turns"] += 1
    state["history"].append({"role": "user", "text": text})
    memory_notes = _load_memory(state, profile)
    delta = understand(state, text, trace, today)
    changed, notes = apply_delta(state, delta, today)
    notes = _unknown_place_note(state, delta, today) + notes
    apply_implicit(state, notes)
    qs = clarifying_questions(state, today)
    blocking = [q for q in qs if q["blocking"]]
    if qs:
        state["clarifications"] += len(qs)
    resp = {"questions": qs, "notes": notes, "memory": memory_notes, "implicit": delta.get("implicit_notes", [])}
    if blocking:
        reply = " ".join([*notes[:1], _blocking_reply(state, blocking)]) if delta.get("unknown_place") else _blocking_reply(state, blocking)
        resp.update(reply=reply, plan=None, changes=[])
    else:
        changes = plan(state, changed, trace)
        state["rebuilt_all"] = bool(changes) and not any(t["status"] == "reused" for t in trace.items)
        if state["first_rec_ms"] is None and state["plan"]:
            state["first_rec_ms"] = round((time.time() - state["started"]) * 1000)
        reply = compose_reply(state, notes, changes, memory_notes)
        if delta.get("unknown_place") and notes and notes[0] not in reply:
            reply = notes[0] + " " + reply
        resp.update(reply=reply, plan=state["plan"], changes=changes)
    resp["trace"] = trace.items
    resp["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    state["history"].append({"role": "agent", "text": resp["reply"]})
    del state["history"][:-MAX_HISTORY]
    return finalize(state, resp)


def _resolve_fixes(state: dict) -> None:
    """Turn generic check fixes into a concrete one-click alternative (actionable warnings)."""
    p = state.get("plan")
    if not p:
        return
    cands, sel = state.get("candidates") or {}, state.get("selected") or {}
    for c in p["checks"]:
        f = c.get("fix")
        if not f or "kind" not in f or "pref" not in f:  # say-fixes and concrete picks need no resolving
            continue
        opts = [o for o in cands.get(f["kind"]) or [] if o["id"] != sel.get(f["kind"])]

        def hr(o, key):
            return int(o[key][11:13]) if key in o else 12

        cur = next((o for o in cands.get(f["kind"]) or [] if o["id"] == sel.get(f["kind"])), None)
        pick = None
        if f["pref"] == "day":
            pick = next((o for o in opts if 9 <= hr(o, "depart") <= 20), None)
        elif f["pref"] == "later" and cur:
            pick = next((o for o in sorted(opts, key=lambda o: o["arrive"]) if o["arrive"] > cur["arrive"] and hr(o, "arrive") <= 20), None)
        elif f["pref"] == "earlier" and cur:
            pick = next((o for o in sorted(opts, key=lambda o: o["depart"]) if o["depart"] < cur["depart"] and hr(o, "depart") >= 7), None)
        elif f["pref"] == "any":
            pick = opts[0] if opts else None
        if pick:
            f["id"] = pick["id"]
            dt = (pick.get("depart") or "")[11:16]
            f["label"] += f" ({dt}, {rub(pick['total'])})" if dt else f" ({rub(pick['total'])})"
        else:
            c.pop("fix")


def finalize(state: dict, resp: dict) -> dict:
    _resolve_fixes(state)
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
    if kind not in SELECTABLE:
        raise AgentInputError(f"Неизвестный тип замены: {kind}")
    if kind == "destination":
        if not ds.destination(item_id):
            raise AgentInputError("Такого направления нет")
    else:
        if not state.get("plan"):
            raise AgentInputError("Сначала нужен план — опишите поездку")
        if item_id not in [o["id"] for o in state["candidates"].get(SELECTABLE[kind]) or []]:
            raise AgentInputError("Этого варианта нет среди предложенных")
    if kind == "destination":
        state["req"]["destination"] = item_id
        state["sources"]["destination"] = "выбор пользователя"
        changed.add("destination")
    elif kind in ("hotel", "outbound", "return", "transfer_out", "transfer_ret"):
        sel[kind] = item_id
        if kind == "hotel":
            state["candidates"]["transfers_out"] = []  # force transfer recompute
    changes = plan(state, changed, trace) if kind == "destination" else _reassemble(state, trace, kind)
    reply = f"Готово, заменил {KIND_RU.get(kind, kind)} и пересобрал план со стыковками. " + " ".join(changes)
    return finalize(state, _action_resp(state, reply, changes, trace))


def _action_resp(state: dict, reply: str, changes: list[str], trace: Trace) -> dict:
    return {"reply": reply, "questions": [], "plan": state["plan"], "changes": changes, "trace": trace.items, "notes": [], "memory": []}


def _reassemble(state: dict, trace: Trace, kind: str) -> list[str]:
    if kind in ("hotel", "outbound", "return"):  # transfers depend on hotel address and arrival time
        state["candidates"].pop("transfers_out", None)
    return plan(state, set(), trace)


def correct(state: dict, slot: str) -> dict:
    """User removes a requirement the agent inferred/was told (agent logic is editable)."""
    trace = Trace()
    req = state["req"]
    if slot not in req:
        raise AgentInputError("Такого условия нет в запросе")
    req.pop(slot, None)
    state.setdefault("removed", []).append(slot)
    state["sources"].pop(slot, None)
    if slot == "destination":
        state["selected"].pop("destination", None)
    apply_implicit(state, [])
    changes = plan(state, {slot}, trace) if req.get("start_date") else []
    return finalize(state, _action_resp(state, f"Убрал условие «{slot}» и пересобрал план.", changes, trace))
