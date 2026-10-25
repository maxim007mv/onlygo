"""Trip assembly: glue the chosen legs into one plan and check every connection (arrival → check-in → check-out → return).

Also the simulated payment. Nothing here talks to a real supplier or charges money.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from . import datastore as ds
from .tools import BAGGAGE_EXIT_MIN, CHECKIN_HOUR, LOCAL_TZ, nights, pax, rub

CHECKOUT_HOUR = 12
TRAIN_STATION_BUFFER_MIN = 40  # leave hotel → board the train
BUFFER_TRAIN_MIN, BUFFER_DOMESTIC_MIN, BUFFER_INTL_MIN = 90, 150, 180

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


@dataclass(frozen=True)
class Legs:
    """The five bookable parts of a trip, as returned by the search tools."""

    hotel: dict
    outbound: dict
    ret: dict
    transfer_out: dict
    transfer_ret: dict


def _check(level: str, title: str, text: str) -> dict:
    return dict(level=level, title=title, text=text)


def _checkin_date(arr: datetime, start: date, checks: list[dict]) -> date:
    if arr.date() <= start:
        return start
    checks.append(
        _check(
            "error_fixed",
            "Дата заезда",
            f"Прилёт {arr:%d.%m %H:%M} — уже следующий день. Сдвинул заезд в отель на {arr.date():%d.%m}, чтобы не платить за пустую ночь.",
        )
    )
    return arr.date()


def _arrival_check(arr: datetime, legs: Legs, checkin_date: date) -> dict:
    tr, early_ok = legs.transfer_out, legs.hotel["early_checkin_available"]
    at_hotel = arr + timedelta(minutes=tr["minutes"] + BAGGAGE_EXIT_MIN)
    checkin_dt = datetime.combine(checkin_date, datetime.min.time()) + timedelta(hours=CHECKIN_HOUR)
    if at_hotel < checkin_dt - timedelta(hours=3) and at_hotel.hour < 6 and at_hotel.date() == checkin_date:
        tail = (
            "В отеле доступен ранний заезд — могу запросить после покупки."
            if early_ok
            else "Ранний заезд не гарантирован; вариант — оплатить предыдущую ночь."
        )
        return _check("warn", "Ранний приезд", f"Будете в отеле около {at_hotel:%H:%M}, заселение с 14:00. " + tail)
    if at_hotel < checkin_dt - timedelta(hours=3):
        tail = "можно запросить ранний заезд." if early_ok else "можно оставить багаж и пойти гулять."
        return _check("warn", "Ожидание заселения", f"Будете в отеле в {at_hotel:%H:%M}, заселение с 14:00 — " + tail)
    if at_hotel.hour >= 23 or at_hotel.date() > checkin_date:
        return _check("warn", "Поздний заезд", f"Приезд в отель около {at_hotel:%H:%M}. Отель предупредим о позднем заселении.")
    return _check(
        "ok",
        "Прилёт → трансфер → заселение",
        f"Прилёт {arr:%H:%M}, трансфер {tr['minutes']} мин, в отеле ~{at_hotel:%H:%M} (заселение с 14:00).",
    )


def _return_buffer(ret_t: dict, dest_id: str) -> int:
    if ret_t["mode"] == "train":
        return BUFFER_TRAIN_MIN
    return BUFFER_DOMESTIC_MIN if ds.destination(dest_id)["country"] == "Россия" else BUFFER_INTL_MIN


def _departure_check(legs: Legs, dest_id: str, checkout_date: date) -> dict:
    ret_t = legs.ret
    dep = datetime.fromisoformat(ret_t["depart"])
    buffer = _return_buffer(ret_t, dest_id)
    to_station = buffer if ret_t["mode"] == "flight" else TRAIN_STATION_BUFFER_MIN
    leave = dep - timedelta(minutes=to_station + legs.transfer_ret["minutes"])
    checkout_dt = datetime.combine(checkout_date, datetime.min.time()) + timedelta(hours=CHECKOUT_HOUR)
    if leave.date() < checkout_date:
        return _check(
            "error", "Обратный рейс", f"Выезжать нужно {leave:%d.%m %H:%M} — раньше даты выезда из отеля. Последняя ночь пропадёт."
        )
    if leave.hour < 6:
        return _check(
            "warn",
            "Ночной выезд",
            f"Обратное отправление в {dep:%H:%M}: выезжать из отеля нужно в {leave:%H:%M} ночью. Последняя ночь "
            "оплачена, но выспаться не получится — можно выбрать другой рейс.",
        )
    if leave > checkout_dt + timedelta(hours=3):
        return _check(
            "warn",
            "Время до вылета",
            f"Выезд из отеля в 12:00, а выезжать в аэропорт/на вокзал только в {leave:%H:%M}. Можно запросить "
            "поздний выезд или оставить багаж.",
        )
    return _check(
        "ok",
        "Выезд → трансфер → обратный рейс",
        f"Выезд из отеля до 12:00, в {leave:%H:%M} — трансфер, отправление {dep:%H:%M} (запас {buffer} мин).",
    )


def _party_checks(req: dict, legs: Legs) -> list[dict]:
    out = []
    if req.get("pets"):
        ok = (
            legs.hotel["pets_allowed"]
            and legs.outbound["pets_in_cabin"]
            and legs.ret["pets_in_cabin"]
            and legs.transfer_out["pets_allowed"]
        )
        text = (
            "Отель, транспорт и трансфер допускают животных."
            if ok
            else "Не все сегменты допускают животных: в самолёте — в салоне до 8 кг в переноске, крупнее — в "
            "багажном отсеке (оформить заранее, ветпаспорт + чип)."
        )
        out.append(_check("ok" if ok else "warn", "Животное", text))
    _, children = pax(req)
    if children and legs.transfer_out["type"] == "taxi":
        out.append(_check("ok", "Ребёнок", "Детское кресло в трансфере включено."))
    return out


def _transport_item(kind: str, t: dict, way: str) -> dict:
    mode = "Перелёт" if t["mode"] == "flight" else "Поезд"
    return dict(
        kind=kind,
        id=t["id"],
        title=f"{mode} {way}: {t['from_name']} → {t['to_name']}",
        sub=f"{t['carrier']}, {t['depart'].replace('T', ' ')}",
        price=t["total"],
    )


def _items(req: dict, legs: Legs, checkin_date: date, checkout_date: date) -> list[dict]:
    hotel, n = legs.hotel, (checkout_date - checkin_date).days
    pet_fee = hotel["pet_fee_per_night"] * n if req.get("pets") else 0
    hotel_total = int(round(hotel["price_per_night_calc"] * n + pet_fee, -2))
    tr_out, tr_ret = legs.transfer_out, legs.transfer_ret
    return [
        _transport_item("outbound", legs.outbound, "туда"),
        dict(
            kind="transfer_out",
            id=tr_out["id"],
            title=f"Трансфер до отеля: {tr_out['title']}",
            sub=f"~{tr_out['minutes']} мин",
            price=tr_out["total"],
        ),
        dict(
            kind="hotel",
            id=hotel["id"],
            title=f"Отель {hotel['name']} {hotel['stars']}★",
            sub=f"{checkin_date:%d.%m} – {checkout_date:%d.%m}, {n} ноч., {hotel['meals_ru']}",
            price=hotel_total,
        ),
        dict(
            kind="transfer_ret",
            id=tr_ret["id"],
            title=f"Трансфер в аэропорт/вокзал: {tr_ret['title']}",
            sub=f"~{tr_ret['minutes']} мин",
            price=tr_ret["total"],
        ),
        _transport_item("return", legs.ret, "обратно"),
    ]


def _budget_check(total: int, budget: int | None) -> list[dict]:
    if not budget:
        return []
    if total > budget:
        return [_check("warn", "Бюджет", f"Итого {rub(total)} — на {rub(total - budget)} больше бюджета {rub(budget)}.")]
    return [_check("ok", "Бюджет", f"Итого {rub(total)} — в рамках бюджета {rub(budget)} (запас {rub(budget - total)}).")]


def assemble_trip(req: dict, dest: dict, legs: Legs) -> dict:
    """Build the plan and its connection checks; every warn/error check carries a one-click fix when one exists."""
    checks: list[dict] = []
    arr = datetime.fromisoformat(legs.outbound["arrive"])
    start = date.fromisoformat(req["start_date"])
    checkin_date = _checkin_date(arr, start, checks)
    checkout_date = start + timedelta(days=nights(req))
    checks.append(_arrival_check(arr, legs, checkin_date))
    checks.append(_departure_check(legs, dest["id"], checkout_date))
    checks += _party_checks(req, legs)
    items = _items(req, legs, checkin_date, checkout_date)
    total = sum(i["price"] for i in items)
    checks += _budget_check(total, req.get("budget"))
    _add_fixes(checks)
    return dict(
        destination=dest,
        items=items,
        total=total,
        checks=checks,
        budget=req.get("budget"),
        start_date=checkin_date.isoformat(),
        end_date=checkout_date.isoformat(),
        nights=(checkout_date - checkin_date).days,
        connection_errors=sum(1 for c in checks if c["level"] == "error"),
    )


def _add_fixes(checks: list[dict]) -> None:
    for c in checks:
        if c["level"] in ("warn", "error") and c["title"] in FIXES:
            c["fix"] = dict(FIXES[c["title"]])


def simulate_payment(plan: dict, payer: dict) -> dict:
    """Fake acquiring: card ending 0002 is declined, anything else «paid». No money moves."""
    card = (payer.get("card") or "").replace(" ", "")
    if card.endswith("0002"):
        return dict(status="declined", reason="Тестовая карта отклонена банком (симуляция). Используйте 4242 4242 4242 4242.")
    bid = "OG-" + uuid.uuid4().hex[:8].upper()
    return dict(
        status="paid",
        booking_id=bid,
        amount=plan["total"],
        paid_at=datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
        vouchers=[dict(item=i["title"], confirmation=f"{bid}-{n + 1}") for n, i in enumerate(plan["items"])],
        note="Оплата симулирована. Реальные деньги не списываются.",
    )
