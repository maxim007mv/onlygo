"""Agent tools. Each tool is a pure function over the mock data layer and the
trip requirements (`req`). The agent orchestrator calls them and logs a trace."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from . import datastore as ds

DEFAULT_NIGHTS = 7
DEFAULT_ADULTS = 2
INFANT_MAX_AGE = 1        # 0–1 y.o.: no seat, ~10% fare
INFANT_FARE_SHARE = 0.1
CHECKIN_HOUR = 14
BAGGAGE_EXIT_MIN = 40     # время на выход из аэропорта/вокзала с багажом

MEAL_RU = {"RO": "без питания", "BB": "завтраки", "HB": "полупансион", "AI": "всё включено"}


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
def suggest_destinations(req: dict, limit: int = 3) -> list[dict]:
    orig = ds.origin(req.get("origin") or "moscow")
    month = req.get("month") or date.fromisoformat(req["start_date"]).month
    moods = set(req.get("moods") or [])
    adults, children = pax(req)
    res = []
    for d in ds.destinations():
        if d["id"] in (req.get("exclude_destinations") or []):
            continue
        c = ds.climate(d, month)
        hours = ds.flight_hours(orig, d)
        est = estimate_cost(req, d, orig, month)
        score, why, cons, risks = 50.0, [], [], []
        hard_fail = False
        if "warm" in moods or "beach" in moods:
            if c["air"] >= 26: score += 18; why.append(f"тепло: ~{c['air']:.0f}°C днём")
            elif c["air"] >= 22: score += 6; why.append(f"комфортно: ~{c['air']:.0f}°C"); cons.append("не жара, скорее мягкое тепло")
            else: score -= 35; cons.append(f"прохладно (~{c['air']:.0f}°C)")
        if "sea" in moods or "beach" in moods:
            if c["sea"] is None: score -= 45; hard_fail = True
            elif c["sea"] >= 24: score += 15; why.append(f"море ~{c['sea']:.0f}°C — можно купаться")
            elif c["sea"] >= 21: score += 3; cons.append(f"море прохладное, ~{c['sea']:.0f}°C")
            else: score -= 30; cons.append(f"море холодное (~{c['sea']:.0f}°C)")
        for tag in moods - {"warm", "sea"}:
            if tag in d["tags"]: score += 8; why.append({"beach": "хорошие пляжи", "city": "интересный город", "food": "сильная гастрономия", "calm": "спокойная атмосфера", "party": "есть ночная жизнь", "romantic": "подходит для романтики", "nature": "красивая природа", "mountains": "горы", "ski": "горнолыжные трассы", "luxury": "уровень люкс", "diving": "дайвинг/снорклинг"}.get(tag, tag))
        if "ski" in moods and month not in (12, 1, 2, 3, 4) and "ski" in d["tags"]:
            score -= 30; cons.append("в эти месяцы не сезон катания")
        if c["rainy"]: score -= 12; risks.append("сезон дождей — возможны ливни")
        maxh = req.get("max_travel_hours")
        if req.get("prefer_train"):
            if d["country"] not in ("Россия", "Абхазия") or d["id"] == "kaliningrad": score -= 30; cons.append("нет поезда")
        if maxh:
            if hours <= maxh: score += 10; why.append(f"перелёт ~{hours:.0f} ч — в пределах {maxh} ч")
            elif hours <= maxh + 1.5: score -= 8; cons.append(f"перелёт ~{hours:.1f} ч, чуть дольше желаемых {maxh} ч")
            else: score -= 40; hard_fail = True; cons.append(f"перелёт ~{hours:.0f} ч — слишком долго")
        else:
            score -= max(0, hours - 6) * 3
        if req.get("visa_free_only") and not d["visa_free"]: score -= 50; hard_fail = True
        if not d["visa_free"]: risks.append("нужна виза/e-visa — заложите время на оформление")
        if children:
            score += (d["kid_score"] - 0.6) * 25
            if d["kid_score"] >= 0.85: why.append("отлично для детей (отели с детклубами, пологий вход в море)")
        if req.get("pets"):
            score += (d["pet_score"] - 0.5) * 40
            if not any(h["pets_allowed"] for h in ds.hotels(d["id"])):
                hard_fail = True; cons.append("нет отелей, где можно с животными")
            if d["pet_score"] < 0.3: cons.append("мало отелей, где можно с животными"); risks.append("ввоз животного может требовать справок/чипирования")
        budget = req.get("budget")
        if budget:
            if est > budget * 1.1: score -= min(40, (est / budget - 1) * 60); cons.append(f"оценка {rub(est)} выше бюджета")
            else: score += 8; why.append(f"укладываемся в бюджет (~{rub(est)})")
        elif req.get("price_sensitive"):
            score -= (est / (adults + children)) / 6000
            if est / (adults + children) < 45000: why.append(f"недорого: ~{rub(est)} за всех")
        if req.get("country") and d["country"] != req["country"]:
            score -= 60; hard_fail = True
        if req.get("meals") == "AI" and "allinclusive" in d["tags"]:
            score += 10; why.append("сильный выбор отелей «всё включено»")
        if d["id"] == req.get("destination"): score += 100
        if not maxh:
            (why if hours <= 4.5 else cons).append(f"дорога ~{hours:.1f} ч из {orig['name']}")
        if d["visa_free"] and (req.get("visa_free_only") or d["country"] != "Россия"):
            why.append("без визы для граждан РФ")
        if budget:
            share = est / budget
            (why if share <= 1 else cons).append(f"оценка {rub(est)} = {share*100:.0f}% бюджета")
        factors = []
        if children: factors.append(f"дети: kid-score {d['kid_score']:.2f}")
        if req.get("pets"): factors.append(f"питомец: pet-score {d['pet_score']:.2f}")
        if req.get("visa_free_only"): factors.append("только без визы")
        if budget or req.get("price_sensitive"): factors.append("цена")
        res.append(dict(
            id=d["id"], factors=factors, name=d["name"], country=d["country"], score=round(score, 1), hard_fail=hard_fail,
            weather=c, flight_hours=hours, visa_free=d["visa_free"], est_total=est, tags=d["tags"],
            why=why[:5], tradeoffs=cons[:4], risks=risks[:3],
        ))
    res.sort(key=lambda x: (-x["score"]))
    good = [r for r in res if not r["hard_fail"]] or res
    return good[:limit]


def estimate_cost(req: dict, d: dict, orig: dict, month: int) -> int:
    adults, children = pax(req)
    dist = ds.distance_km(orig["lat"], orig["lon"], d["lat"], d["lon"])
    sm = ds.season_mult(d, month)
    flight = (3500 + dist * 4.2) * sm * 2 * (adults + child_fare_units(req, 0.75))
    hotel = d["base_hotel_price"] * sm * nights(req) * (1 + 0.25 * children)
    return int(round(flight + hotel + 4000, -3))


# ---------------------------------------------------------------- hotels
def search_hotels(req: dict, did: str, limit: int = 3) -> list[dict]:
    d = ds.destination(did)
    start = date.fromisoformat(req["start_date"])
    sm = ds.season_mult(d, start.month)
    adults, children = pax(req)
    n = nights(req)
    budget = req.get("budget")
    hotel_budget = budget * 0.55 if budget else None
    items = []
    for h in ds.hotels(did):
        if req.get("pets") and not h["pets_allowed"]:
            continue
        price_n = h["price_per_night"] * sm * (1 + 0.2 * children)
        total = price_n * n + (h["pet_fee_per_night"] * n if req.get("pets") else 0)
        score, why, cons, risks = h["rating"] * 6, [], [], []
        why.append(f"рейтинг {h['rating']} ({h['reviews']} отзывов)")
        if hotel_budget:
            if total <= hotel_budget: score += 10
            else: score -= (total / hotel_budget - 1) * 50; cons.append("дороже ориентира по бюджету")
        if req.get("price_sensitive"):
            score -= total / max(1, n) / 1500
        if req.get("meals"):
            if h["meals"] == req["meals"]: score += 15; why.append(f"есть желаемое питание: {MEAL_RU[h['meals']]}")
            else: score -= 8; cons.append(f"питание: {MEAL_RU[h['meals']]} (а вы хотели {MEAL_RU[req['meals']]})")
        elif children and h["meals"] in ("AI", "HB"):
            score += 8; why.append(f"{MEAL_RU[h['meals']]} — с ребёнком удобно не искать, где поесть")
        if h["free_cancellation"]:
            score += 10 if (req.get("flexible_cancellation") or children) else 4
            why.append(f"бесплатная отмена за {h['cancellation_deadline_days']} дн. до заезда")
        else:
            score -= 10 if req.get("flexible_cancellation") else 2
            risks.append("невозвратный тариф — при отмене деньги не вернут")
        if h["distance_to_beach_m"] is not None and "sea" in d["tags"]:
            dm = h["distance_to_beach_m"]
            if dm <= 200: score += 10 if (children or req.get("beach_close")) else 5; why.append(f"{dm} м до пляжа")
            elif dm >= 1000: score -= 8 if children else 4; cons.append(f"до пляжа {dm/1000:.1f} км — нужен транспорт")
        if req.get("center_close"):
            score += 8 if h["distance_to_center_km"] < 2 else -5
        if children:
            if h["kids_club"]: score += 6; why.append("детский клуб")
            if h["family_rooms"]: score += 3
        if req.get("pets"):
            why.append("можно с животными" + (f" (доплата {rub(h['pet_fee_per_night'])}/ночь)" if h["pet_fee_per_night"] else " (без доплаты)"))
        if h["rating"] < 7.8: risks.append("рейтинг ниже 8 — проверьте свежие отзывы")
        items.append(dict(**h, price_per_night_calc=int(price_n), total=int(round(total, -2)), meals_ru=MEAL_RU[h["meals"]],
                          score=round(score, 1), why=why[:5], tradeoffs=cons[:3], risks=risks[:3]))
    items.sort(key=lambda x: -x["score"])
    return items[:limit]


# ---------------------------------------------------------------- transport
def search_transport(req: dict, did: str, direction: str, limit: int = 3) -> list[dict]:
    d = ds.destination(did)
    orig = ds.origin(req.get("origin") or "moscow")
    start = date.fromisoformat(req["start_date"])
    day = start if direction == "out" else start + timedelta(days=nights(req))
    adults, children = pax(req)
    opts = ds.transport_options(orig, d, day, direction)
    res = []
    for o in opts:
        total = o["price_per_adult"] * (adults + child_fare_units(req, o["price_per_child"] / max(1, o["price_per_adult"])))
        dep = datetime.fromisoformat(o["depart"])
        score, why, cons, risks = 100 - total / 2000, [], [], []
        if req.get("budget") or req.get("price_sensitive"):
            score -= total / 2500
        h = o["duration_min"] / 60
        maxh = req.get("max_travel_hours")
        if o["mode"] == "train":
            if req.get("prefer_train"): score += 40; why.append("поезд, как вы хотели")
            elif maxh and h > maxh: score -= 30
            else: score -= 10
            why.append(f"в пути {h:.0f} ч, ж/д — меньше ограничений по багажу и животным")
        else:
            if req.get("prefer_train"): score -= 25
            if o["stops"] == 0: score += 15; why.append(f"прямой рейс, {h:.1f} ч")
            else:
                score -= 15 if (children or req.get("direct_only")) else 6
                cons.append(f"1 пересадка, всего {h:.1f} ч")
                if req.get("direct_only"): score -= 40
            if maxh and h > maxh: score -= 20; cons.append(f"дольше желаемых {maxh} ч")
        if dep.hour < 5 or dep.hour >= 23:
            score -= 15 if children else 5
            cons.append(f"ночной вылет в {dep:%H:%M}" + (" — тяжело с ребёнком" if children else ""))
        elif direction == "ret" and dep.hour < 8:
            score -= 12 if children else 5
            cons.append(f"ранний вылет в {dep:%H:%M} — выезд из отеля ночью")
        else:
            why.append(f"отправление в {dep:%H:%M}")
        if o["baggage_included"]: score += 4; why.append("багаж включён")
        else: cons.append("багаж оплачивается отдельно")
        if not o["refundable"]: risks.append("невозвратный билет")
        if req.get("pets"):
            if o["pets_in_cabin"]: why.append("можно с животным в салоне/купе")
            else: score -= 30; risks.append("перевозка животного только в багажном отсеке")
        res.append(dict(**o, total=int(total), score=round(score, 1), why=why[:4], tradeoffs=cons[:3], risks=risks[:3]))
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
            if children: score += 15; why.append("детское кресло включено")
            if late: score += 15; why.append("работает ночью")
        if req.get("pets") and not t["pets_allowed"]: score -= 50; cons.append("нельзя с животными")
        if late and not t["available_24h"]: score -= 40; cons.append("не работает при позднем прилёте")
        if cars > 1: score -= 10; cons.append(f"нужно {cars} машины на {people} чел.")
        res.append(dict(**t, total=int(price), minutes=minutes, score=round(score, 1), why=why, tradeoffs=cons))
    res.sort(key=lambda x: -x["score"])
    return res


# ---------------------------------------------------------------- assembly & checks
def assemble_trip(req: dict, dest: dict, hotel: dict, out_t: dict, ret_t: dict, tr_out: dict, tr_ret: dict) -> dict:
    checks = []
    arr = datetime.fromisoformat(out_t["arrive"])
    at_hotel = arr + timedelta(minutes=tr_out["minutes"] + BAGGAGE_EXIT_MIN)
    start = date.fromisoformat(req["start_date"])
    checkin_date = start
    if arr.date() > start:
        checkin_date = arr.date()
        checks.append(dict(level="error_fixed", title="Дата заезда", text=f"Прилёт {arr:%d.%m %H:%M} — уже следующий день. Сдвинул заезд в отель на {checkin_date:%d.%m}, чтобы не платить за пустую ночь."))
    checkin_dt = datetime.combine(checkin_date, datetime.min.time()) + timedelta(hours=CHECKIN_HOUR)
    if at_hotel < checkin_dt - timedelta(hours=3):
        if at_hotel.hour < 6 and at_hotel.date() == checkin_date:
            checks.append(dict(level="warn", title="Ранний приезд", text=f"Будете в отеле около {at_hotel:%H:%M}, заселение с 14:00. " + ("В отеле доступен ранний заезд — могу запросить после покупки." if hotel["early_checkin_available"] else "Ранний заезд не гарантирован; вариант — оплатить предыдущую ночь.")))
        else:
            checks.append(dict(level="warn", title="Ожидание заселения", text=f"Будете в отеле в {at_hotel:%H:%M}, заселение с 14:00 — " + ("можно запросить ранний заезд." if hotel["early_checkin_available"] else "можно оставить багаж и пойти гулять.")))
    elif at_hotel.hour >= 23 or at_hotel.date() > checkin_date:
        checks.append(dict(level="warn", title="Поздний заезд", text=f"Приезд в отель около {at_hotel:%H:%M}. Отель предупредим о позднем заселении."))
    else:
        checks.append(dict(level="ok", title="Прилёт → трансфер → заселение", text=f"Прилёт {arr:%H:%M}, трансфер {tr_out['minutes']} мин, в отеле ~{at_hotel:%H:%M} (заселение с 14:00)."))
    dep = datetime.fromisoformat(ret_t["depart"])
    buffer = 90 if ret_t["mode"] == "train" else (150 if ds.destination(dest["id"])["country"] == "Россия" else 180)
    leave = dep - timedelta(minutes=buffer if ret_t["mode"] == "flight" else 40) - timedelta(minutes=tr_ret["minutes"])
    checkout_date = start + timedelta(days=nights(req))
    checkout_dt = datetime.combine(checkout_date, datetime.min.time()) + timedelta(hours=12)
    if leave.date() < checkout_date:
        checks.append(dict(level="error", title="Обратный рейс", text=f"Выезжать нужно {leave:%d.%m %H:%M} — раньше даты выезда из отеля. Последняя ночь пропадёт."))
    elif leave.hour < 6:
        checks.append(dict(level="warn", title="Ночной выезд", text=f"Обратное отправление в {dep:%H:%M}: выезжать из отеля нужно в {leave:%H:%M} ночью. Последняя ночь оплачена, но выспаться не получится — можно выбрать другой рейс."))
    elif leave > checkout_dt + timedelta(hours=3):
        checks.append(dict(level="warn", title="Время до вылета", text=f"Выезд из отеля в 12:00, а выезжать в аэропорт/на вокзал только в {leave:%H:%M}. Можно запросить поздний выезд или оставить багаж."))
    else:
        checks.append(dict(level="ok", title="Выезд → трансфер → обратный рейс", text=f"Выезд из отеля до 12:00, в {leave:%H:%M} — трансфер, отправление {dep:%H:%M} (запас {buffer} мин)."))
    if req.get("pets"):
        ok = hotel["pets_allowed"] and out_t["pets_in_cabin"] and ret_t["pets_in_cabin"] and tr_out["pets_allowed"]
        checks.append(dict(level="ok" if ok else "warn", title="Животное", text="Отель, транспорт и трансфер допускают животных." if ok else "Не все сегменты допускают животных: в самолёте — в салоне до 8 кг в переноске, крупнее — в багажном отсеке (оформить заранее, ветпаспорт + чип)."))
    adults, children = pax(req)
    if children and tr_out["type"] == "taxi":
        checks.append(dict(level="ok", title="Ребёнок", text="Детское кресло в трансфере включено."))
    hotel_nights = (checkout_date - checkin_date).days
    hotel_total = int(round(hotel["price_per_night_calc"] * hotel_nights + (hotel["pet_fee_per_night"] * hotel_nights if req.get("pets") else 0), -2))
    items = [
        dict(kind="outbound", id=out_t["id"], title=f"{'Перелёт' if out_t['mode']=='flight' else 'Поезд'} туда: {out_t['from_name']} → {out_t['to_name']}", sub=f"{out_t['carrier']}, {out_t['depart'].replace('T',' ')}", price=out_t["total"]),
        dict(kind="transfer_out", id=tr_out["id"], title=f"Трансфер до отеля: {tr_out['title']}", sub=f"~{tr_out['minutes']} мин", price=tr_out["total"]),
        dict(kind="hotel", id=hotel["id"], title=f"Отель {hotel['name']} {hotel['stars']}★", sub=f"{checkin_date:%d.%m} – {checkout_date:%d.%m}, {hotel_nights} ноч., {hotel['meals_ru']}", price=hotel_total),
        dict(kind="transfer_ret", id=tr_ret["id"], title=f"Трансфер в аэропорт/вокзал: {tr_ret['title']}", sub=f"~{tr_ret['minutes']} мин", price=tr_ret["total"]),
        dict(kind="return", id=ret_t["id"], title=f"{'Перелёт' if ret_t['mode']=='flight' else 'Поезд'} обратно: {ret_t['from_name']} → {ret_t['to_name']}", sub=f"{ret_t['carrier']}, {ret_t['depart'].replace('T',' ')}", price=ret_t["total"]),
    ]
    total = sum(i["price"] for i in items)
    budget = req.get("budget")
    if budget:
        if total > budget:
            checks.append(dict(level="warn", title="Бюджет", text=f"Итого {rub(total)} — на {rub(total-budget)} больше бюджета {rub(budget)}."))
        else:
            checks.append(dict(level="ok", title="Бюджет", text=f"Итого {rub(total)} — в рамках бюджета {rub(budget)} (запас {rub(budget-total)})."))
    _add_fixes(checks)
    return dict(destination=dest, items=items, total=total, checks=checks, budget=budget,
                start_date=checkin_date.isoformat(), end_date=checkout_date.isoformat(), nights=hotel_nights,
                connection_errors=sum(1 for c in checks if c["level"] == "error"))


def simulate_payment(plan: dict, payer: dict) -> dict:
    card = (payer.get("card") or "").replace(" ", "")
    if card.endswith("0002"):
        return dict(status="declined", reason="Тестовая карта отклонена банком (симуляция). Используйте 4242 4242 4242 4242.")
    bid = "OG-" + uuid.uuid4().hex[:8].upper()
    return dict(status="paid", booking_id=bid, amount=plan["total"], paid_at=datetime.now().isoformat(timespec="seconds"),
                vouchers=[dict(item=i["title"], confirmation=f"{bid}-{n+1}") for n, i in enumerate(plan["items"])],
                note="Оплата симулирована. Реальные деньги не списываются.")


FIXES = {
    "Ранний приезд": dict(label="Взять рейс попозже", kind="outbound", pref="later"),
    "Ожидание заселения": dict(label="Взять рейс попозже", kind="outbound", pref="later"),
    "Поздний заезд": dict(label="Взять рейс пораньше", kind="outbound", pref="earlier"),
    "Ночной выезд": dict(label="Взять дневной обратный рейс", kind="return", pref="day"),
    "Обратный рейс": dict(label="Взять дневной обратный рейс", kind="return", pref="day"),
    "Время до вылета": dict(label="Взять рейс пораньше", kind="return", pref="earlier"),
    "Бюджет": dict(label="Сделать дешевле", say="бюджет меньше"),
    "Животное": dict(label="Подобрать pet-friendly трансфер", kind="transfer_out", pref="any"),
}


def _add_fixes(checks: list[dict]) -> None:
    for c in checks:
        if c["level"] in ("warn", "error") and c["title"] in FIXES:
            c["fix"] = dict(FIXES[c["title"]])
