"""Agent tools. Each tool is a pure function over the mock data layer and the
trip requirements (`req`). The agent orchestrator calls them and logs a trace."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import datastore as ds

DEFAULT_NIGHTS = 7
DEFAULT_ADULTS = 2
INFANT_MAX_AGE = 1  # 0–1 y.o.: no seat, ~10% fare
INFANT_FARE_SHARE = 0.1
CHECKIN_HOUR = 14
BAGGAGE_EXIT_MIN = 40  # время на выход из аэропорта/вокзала с багажом

MEAL_RU = {"RO": "без питания", "BB": "завтраки", "HB": "полупансион", "AI": "всё включено"}


def _local_tz() -> timezone | ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TZ", "Europe/Moscow"))
    except (ZoneInfoNotFoundError, ValueError):  # slim image without tzdata
        return timezone(timedelta(hours=3), "MSK")


LOCAL_TZ = _local_tz()


def today_local() -> date:
    """«Сегодня» in the traveller's zone (default Moscow), not the server's UTC clock."""
    return datetime.now(LOCAL_TZ).date()


def rub(x: float) -> str:
    return f"{int(round(x)):,}".replace(",", " ") + " ₽"


def pax(req: dict) -> tuple[int, int]:
    return req.get("adults") or DEFAULT_ADULTS, req.get("children") or 0


def child_fare_units(req: dict, child_share: float) -> float:
    """Sum of child fare multipliers: infants (0–1) fly for ~10%, older kids pay `child_share`."""
    _, children = pax(req)
    ages = list(req.get("child_ages") or [])[:children]
    infants = sum(1 for a in ages if a <= INFANT_MAX_AGE)
    return infants * INFANT_FARE_SHARE + (children - infants) * child_share


def nights(req: dict) -> int:
    return req.get("nights") or DEFAULT_NIGHTS


# ---------------------------------------------------------------- destinations
TAG_WHY = {
    "beach": "хорошие пляжи",
    "city": "интересный город",
    "food": "сильная гастрономия",
    "calm": "спокойная атмосфера",
    "party": "есть ночная жизнь",
    "romantic": "подходит для романтики",
    "nature": "красивая природа",
    "mountains": "горы",
    "ski": "горнолыжные трассы",
    "luxury": "уровень люкс",
    "diving": "дайвинг/снорклинг",
}
HOTEL_BUDGET_SHARE = 0.55  # rough share of a package budget that goes to the hotel
SKI_MONTHS = (12, 1, 2, 3, 4)
TRAIN_COUNTRIES = ("Россия", "Абхазия")


@dataclass
class _Score:
    """Accumulates a destination's score and its human-readable reasons (order matters: the UI shows the first few)."""

    d: dict
    c: dict
    hours: float
    est: int
    score: float = 50.0
    hard_fail: bool = False
    why: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)


def _score_climate(s: _Score, moods: set[str], month: int) -> None:
    air, sea = s.c["air"], s.c["sea"]
    if moods & {"warm", "beach"}:
        if air >= 26:
            s.score += 18
            s.why.append(f"тепло: ~{air:.0f}°C днём")
        elif air >= 22:
            s.score += 6
            s.why.append(f"комфортно: ~{air:.0f}°C")
            s.cons.append("не жара, скорее мягкое тепло")
        else:
            s.score -= 35
            s.cons.append(f"прохладно (~{air:.0f}°C)")
    if moods & {"sea", "beach"}:
        _score_sea(s, sea)
    for tag in sorted(moods - {"warm", "sea"}):  # sorted: stable explanations between runs
        if tag in s.d["tags"]:
            s.score += 8
            s.why.append(TAG_WHY.get(tag, tag))
    if "ski" in moods and month not in SKI_MONTHS and "ski" in s.d["tags"]:
        s.score -= 30
        s.cons.append("в эти месяцы не сезон катания")
    if s.c["rainy"]:
        s.score -= 12
        s.risks.append("сезон дождей — возможны ливни")


def _score_sea(s: _Score, sea: float | None) -> None:
    if sea is None:
        s.score -= 45
        s.hard_fail = True
    elif sea >= 24:
        s.score += 15
        s.why.append(f"море ~{sea:.0f}°C — можно купаться")
    elif sea >= 21:
        s.score += 3
        s.cons.append(f"море прохладное, ~{sea:.0f}°C")
    else:
        s.score -= 30
        s.cons.append(f"море холодное (~{sea:.0f}°C)")


def _score_travel(s: _Score, req: dict) -> None:
    d, hours, maxh = s.d, s.hours, req.get("max_travel_hours")
    if req.get("prefer_train") and (d["country"] not in TRAIN_COUNTRIES or d["id"] == "kaliningrad"):
        s.score -= 30
        s.cons.append("нет поезда")
    if not maxh:
        s.score -= max(0, hours - 6) * 3
    elif hours <= maxh:
        s.score += 10
        s.why.append(f"перелёт ~{hours:.0f} ч — в пределах {maxh:g} ч")
    elif hours <= maxh + 1.5:
        s.score -= 8
        s.cons.append(f"перелёт ~{hours:.1f} ч, чуть дольше желаемых {maxh:g} ч")
    else:
        s.score -= 40
        s.hard_fail = True
        s.cons.append(f"перелёт ~{hours:.0f} ч — слишком долго")
    if req.get("visa_free_only") and not d["visa_free"]:
        s.score -= 50
        s.hard_fail = True
    if not d["visa_free"]:
        s.risks.append("нужна виза/e-visa — заложите время на оформление")


def _score_party(s: _Score, req: dict, children: int) -> None:
    d = s.d
    if children:
        s.score += (d["kid_score"] - 0.6) * 25
        if d["kid_score"] >= 0.85:
            s.why.append("отлично для детей (отели с детклубами, пологий вход в море)")
    if not req.get("pets"):
        return
    s.score += (d["pet_score"] - 0.5) * 40
    if not any(h["pets_allowed"] for h in ds.hotels(d["id"])):
        s.hard_fail = True
        s.cons.append("нет отелей, где можно с животными")
    if d["pet_score"] < 0.3:
        s.cons.append("мало отелей, где можно с животными")
        s.risks.append("ввоз животного может требовать справок/чипирования")


def _score_price(s: _Score, req: dict, travellers: int) -> None:
    budget, est = req.get("budget"), s.est
    if budget:
        if est > budget * 1.1:
            s.score -= min(40, (est / budget - 1) * 60)
            s.cons.append(f"оценка {rub(est)} выше бюджета")
        else:
            s.score += 8
            s.why.append(f"укладываемся в бюджет (~{rub(est)})")
    elif req.get("price_sensitive"):
        s.score -= (est / travellers) / 6000
        if est / travellers < 45000:
            s.why.append(f"недорого: ~{rub(est)} за всех")


def _score_wishes(s: _Score, req: dict) -> None:
    d = s.d
    if req.get("country") and d["country"] != req["country"]:
        s.score -= 60
        s.hard_fail = True
    if req.get("meals") == "AI" and "allinclusive" in d["tags"]:
        s.score += 10
        s.why.append("сильный выбор отелей «всё включено»")
    if d["id"] == req.get("destination"):
        s.score += 100


def _summary_notes(s: _Score, req: dict, orig: dict) -> None:
    d, hours, budget = s.d, s.hours, req.get("budget")
    if not req.get("max_travel_hours"):
        (s.why if hours <= 4.5 else s.cons).append(f"дорога ~{hours:.1f} ч из {orig['name']}")
    if d["visa_free"] and (req.get("visa_free_only") or d["country"] != "Россия"):
        s.why.append("без визы для граждан РФ")
    if budget:
        share = s.est / budget
        (s.why if share <= 1 else s.cons).append(f"оценка {rub(s.est)} = {share * 100:.0f}% бюджета")


def _factors(req: dict, d: dict, children: int) -> list[str]:
    """Which personal factors moved the ranking — shown in the trace for explainability."""
    out = []
    if children:
        out.append(f"дети: kid-score {d['kid_score']:.2f}")
    if req.get("pets"):
        out.append(f"питомец: pet-score {d['pet_score']:.2f}")
    if req.get("visa_free_only"):
        out.append("только без визы")
    if req.get("budget") or req.get("price_sensitive"):
        out.append("цена")
    return out


def _score_destination(req: dict, d: dict, orig: dict, month: int) -> dict:
    adults, children = pax(req)
    s = _Score(d=d, c=ds.climate(d, month), hours=ds.flight_hours(orig, d), est=estimate_cost(req, d, orig, month))
    _score_climate(s, set(req.get("moods") or []), month)
    _score_travel(s, req)
    _score_party(s, req, children)
    _score_price(s, req, adults + children)
    _score_wishes(s, req)
    _summary_notes(s, req, orig)
    return dict(
        id=d["id"],
        factors=_factors(req, d, children),
        name=d["name"],
        country=d["country"],
        score=round(s.score, 1),
        hard_fail=s.hard_fail,
        weather=s.c,
        flight_hours=s.hours,
        visa_free=d["visa_free"],
        est_total=s.est,
        tags=d["tags"],
        why=s.why[:5],
        tradeoffs=s.cons[:4],
        risks=s.risks[:3],
    )


def suggest_destinations(req: dict, limit: int = 3) -> list[dict]:
    """Rank catalogue destinations for the request; hard failures (no sea, visa, too far) drop out unless nothing is left."""
    orig = ds.origin(req.get("origin") or "moscow")
    month = req.get("month") or date.fromisoformat(req["start_date"]).month
    excluded = set(req.get("exclude_destinations") or [])
    res = [_score_destination(req, d, orig, month) for d in ds.destinations() if d["id"] not in excluded]
    res.sort(key=lambda x: -x["score"])
    good = [r for r in res if not r["hard_fail"]] or res
    return good[:limit]


def destination_card(req: dict, did: str) -> dict:
    """Score one specific destination (a user's pick), even if it would not make the ranked list."""
    orig = ds.origin(req.get("origin") or "moscow")
    month = req.get("month") or date.fromisoformat(req["start_date"]).month
    return _score_destination(req, ds.destination(did), orig, month)


def estimate_cost(req: dict, d: dict, orig: dict, month: int) -> int:
    adults, children = pax(req)
    dist = ds.distance_km(orig["lat"], orig["lon"], d["lat"], d["lon"])
    sm = ds.season_mult(d, month)
    flight = (3500 + dist * 4.2) * sm * 2 * (adults + child_fare_units(req, 0.75))
    hotel = d["base_hotel_price"] * sm * nights(req) * (1 + 0.25 * children)
    return int(round(flight + hotel + 4000, -3))


# ---------------------------------------------------------------- hotels
def _hotel_price_fit(h: dict, req: dict, total: float, n: int) -> tuple[float, list[str]]:
    score, cons = 0.0, []
    budget = req.get("budget")
    hotel_budget = budget * HOTEL_BUDGET_SHARE if budget else None
    if hotel_budget and total <= hotel_budget:
        score += 10
    elif hotel_budget:
        score -= (total / hotel_budget - 1) * 50
        cons.append("дороже ориентира по бюджету")
    if req.get("price_sensitive"):
        score -= total / max(1, n) / 1500
    return score, cons


def _hotel_comfort(h: dict, req: dict, d: dict, children: int) -> tuple[float, list[str], list[str], list[str]]:
    """Meals, cancellation, beach/center distance, kids and pets — in the order the explanation shows them."""
    score, why, cons, risks = 0.0, [], [], []
    meals = MEAL_RU[h["meals"]]
    if req.get("meals") and h["meals"] == req["meals"]:
        score += 15
        why.append(f"есть желаемое питание: {meals}")
    elif req.get("meals"):
        score -= 8
        cons.append(f"питание: {meals} (а вы хотели {MEAL_RU[req['meals']]})")
    elif children and h["meals"] in ("AI", "HB"):
        score += 8
        why.append(f"{meals} — с ребёнком удобно не искать, где поесть")
    if h["free_cancellation"]:
        score += 10 if (req.get("flexible_cancellation") or children) else 4
        why.append(f"бесплатная отмена за {h['cancellation_deadline_days']} дн. до заезда")
    else:
        score -= 10 if req.get("flexible_cancellation") else 2
        risks.append("невозвратный тариф — при отмене деньги не вернут")
    dm = h["distance_to_beach_m"]
    if dm is not None and "sea" in d["tags"] and dm <= 200:
        score += 10 if (children or req.get("beach_close")) else 5
        why.append(f"{dm} м до пляжа")
    elif dm is not None and "sea" in d["tags"] and dm >= 1000:
        score -= 8 if children else 4
        cons.append(f"до пляжа {dm / 1000:.1f} км — нужен транспорт")
    if req.get("center_close"):
        score += 8 if h["distance_to_center_km"] < 2 else -5
    return score, why, cons, risks


def _hotel_family(h: dict, req: dict, children: int) -> tuple[float, list[str]]:
    score, why = 0.0, []
    if children and h["kids_club"]:
        score += 6
        why.append("детский клуб")
    if children and h["family_rooms"]:
        score += 3
    if req.get("pets"):
        fee = f" (доплата {rub(h['pet_fee_per_night'])}/ночь)" if h["pet_fee_per_night"] else " (без доплаты)"
        why.append("можно с животными" + fee)
    return score, why


def _score_hotel(h: dict, req: dict, d: dict, sm: float) -> dict:
    _, children = pax(req)
    n = nights(req)
    price_n = h["price_per_night"] * sm * (1 + 0.2 * children)
    total = price_n * n + (h["pet_fee_per_night"] * n if req.get("pets") else 0)
    s_price, cons = _hotel_price_fit(h, req, total, n)
    s_comfort, why, cons2, risks = _hotel_comfort(h, req, d, children)
    s_family, why2 = _hotel_family(h, req, children)
    if h["rating"] < 7.8:
        risks.append("рейтинг ниже 8 — проверьте свежие отзывы")
    why = [f"рейтинг {h['rating']} ({h['reviews']} отзывов)", *why, *why2]
    return dict(
        **h,
        price_per_night_calc=int(price_n),
        total=int(round(total, -2)),
        meals_ru=MEAL_RU[h["meals"]],
        score=round(h["rating"] * 6 + s_price + s_comfort + s_family, 1),
        why=why[:5],
        tradeoffs=(cons + cons2)[:3],
        risks=risks[:3],
    )


def search_hotels(req: dict, did: str, limit: int = 3) -> list[dict]:
    """Score the destination's hotels for this traveller (pets filter is hard, the rest is soft)."""
    d = ds.destination(did)
    sm = ds.season_mult(d, date.fromisoformat(req["start_date"]).month)
    items = [_score_hotel(h, req, d, sm) for h in ds.hotels(did) if not (req.get("pets") and not h["pets_allowed"])]
    items.sort(key=lambda x: -x["score"])
    return items[:limit]


# ---------------------------------------------------------------- transport
def _score_mode(o: dict, req: dict, children: int) -> tuple[float, list[str], list[str]]:
    """Train vs flight preference, stops and the travel-time limit."""
    h, maxh, prefer_train = o["duration_min"] / 60, req.get("max_travel_hours"), req.get("prefer_train")
    score, why, cons = 0.0, [], []
    if o["mode"] == "train":
        if prefer_train:
            score += 40
            why.append("поезд, как вы хотели")
        else:
            score -= 30 if (maxh and h > maxh) else 10
        why.append(f"в пути {h:.0f} ч, ж/д — меньше ограничений по багажу и животным")
        return score, why, cons
    if prefer_train:
        score -= 25
    if o["stops"] == 0:
        score += 15
        why.append(f"прямой рейс, {h:.1f} ч")
    else:
        score -= 15 if (children or req.get("direct_only")) else 6
        score -= 40 if req.get("direct_only") else 0
        cons.append(f"1 пересадка, всего {h:.1f} ч")
    if maxh and h > maxh:
        score -= 20
        cons.append(f"дольше желаемых {maxh:g} ч")
    return score, why, cons


def _score_departure(dep: datetime, direction: str, children: int) -> tuple[float, list[str], list[str]]:
    if dep.hour < 5 or dep.hour >= 23:
        return -(15 if children else 5), [], [f"ночной вылет в {dep:%H:%M}" + (" — тяжело с ребёнком" if children else "")]
    if direction == "ret" and dep.hour < 8:
        return -(12 if children else 5), [], [f"ранний вылет в {dep:%H:%M} — выезд из отеля ночью"]
    return 0.0, [f"отправление в {dep:%H:%M}"], []


def _score_fare(o: dict, req: dict) -> tuple[float, list[str], list[str], list[str]]:
    score, why, cons, risks = 0.0, [], [], []
    if o["baggage_included"]:
        score += 4
        why.append("багаж включён")
    else:
        cons.append("багаж оплачивается отдельно")
    if not o["refundable"]:
        risks.append("невозвратный билет")
    if req.get("pets") and o["pets_in_cabin"]:
        why.append("можно с животным в салоне/купе")
    elif req.get("pets"):
        score -= 30
        risks.append("перевозка животного только в багажном отсеке")
    return score, why, cons, risks


def _score_option(o: dict, req: dict, direction: str) -> dict:
    adults, children = pax(req)
    total = o["price_per_adult"] * (adults + child_fare_units(req, o["price_per_child"] / max(1, o["price_per_adult"])))
    score = 100 - total / 2000 - (total / 2500 if (req.get("budget") or req.get("price_sensitive")) else 0)
    s1, why1, cons1 = _score_mode(o, req, children)
    s2, why2, cons2 = _score_departure(datetime.fromisoformat(o["depart"]), direction, children)
    s3, why3, cons3, risks = _score_fare(o, req)
    why, cons = why1 + why2 + why3, cons1 + cons2 + cons3
    return dict(**o, total=int(total), score=round(score + s1 + s2 + s3, 1), why=why[:4], tradeoffs=cons[:3], risks=risks[:3])


def search_transport(req: dict, did: str, direction: str, limit: int = 3) -> list[dict]:
    """Flights/trains for one direction ("out" on the start date, "ret" after the stay), best first."""
    orig = ds.origin(req.get("origin") or "moscow")
    start = date.fromisoformat(req["start_date"])
    day = start if direction == "out" else start + timedelta(days=nights(req))
    res = [_score_option(o, req, direction) for o in ds.transport_options(orig, ds.destination(did), day, direction)]
    res.sort(key=lambda x: -x["score"])
    return res[:limit]


def search_transfers(req: dict, did: str, hotel_id: str | None = None, arrival_iso: str | None = None) -> list[dict]:
    adults, children = pax(req)
    people = adults + children
    h = ds.hotel(hotel_id) if hotel_id else None
    base_min = h["transfer_minutes"] if h else 40  # mock default when hotel unknown
    late = arrival_iso and (datetime.fromisoformat(arrival_iso).hour >= 22 or datetime.fromisoformat(arrival_iso).hour < 6)
    res = []
    for t in ds.transfers(did):
        cars = -(-people // t["max_pax"]) if t["per"] == "car" else 1  # ceil: big groups need several cars
        price = t["price"] * cars if t["per"] == "car" else t["price"] * people
        minutes = int(base_min * t["minutes_factor"])
        score, why, cons = 50 - price / 300 - minutes / 5, [], []
        if t["type"] == "taxi":
            why.append("от двери до двери")
            if children:
                score += 15
                why.append("детское кресло включено")
            if late:
                score += 15
                why.append("работает ночью")
        if req.get("pets") and not t["pets_allowed"]:
            score -= 50
            cons.append("нельзя с животными")
        if late and not t["available_24h"]:
            score -= 40
            cons.append("не работает при позднем прилёте")
        if cars > 1:
            score -= 10
            cons.append(f"нужно {cars} машины на {people} чел.")
        res.append(dict(**t, total=int(price), minutes=minutes, score=round(score, 1), why=why, tradeoffs=cons))
    res.sort(key=lambda x: -x["score"])
    return res
