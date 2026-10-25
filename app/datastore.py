"""Mock data layer. Loads synthetic JSON datasets and generates deterministic
flight/train schedules. Everything is MOCK data, not real inventory."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent / "data"))


@lru_cache
def _load(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def destinations() -> list[dict]:
    return _load("destinations.json")


def destination(did: str) -> dict | None:
    return next((d for d in destinations() if d["id"] == did), None)


def hotels(did: str) -> list[dict]:
    return [h for h in _load("hotels.json") if h["destination_id"] == did]


def hotel(hid: str) -> dict | None:
    return next((h for h in _load("hotels.json") if h["id"] == hid), None)


def transfers(did: str) -> list[dict]:
    return [t for t in _load("transfers.json") if t["destination_id"] == did]


def origins() -> list[dict]:
    return _load("origins.json")


def origin(oid: str) -> dict:
    return next((o for o in origins() if o["id"] == oid), origins()[0])


# ---------- climate (smooth model from Jan/Jul anchors) ----------
def _seasonal(jan: float, jul: float, month: int, lag: float = 0.0) -> float:
    mean, amp = (jan + jul) / 2, (jul - jan) / 2
    return round(mean + amp * math.cos((month - 7 - lag) / 12 * 2 * math.pi), 1)


def climate(d: dict, month: int) -> dict:
    air = _seasonal(d["air_temp_jan"], d["air_temp_jul"], month)
    sea = None
    if d.get("sea_temp_jan") is not None:
        sea = _seasonal(d["sea_temp_jan"], d["sea_temp_jul"], month, lag=1.0)
    return {"air": air, "sea": sea, "rainy": month in d["rainy_months"], "source": "mock-climate"}


def distance_km(a_lat, a_lon, b_lat, b_lon) -> float:
    r = 6371
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def flight_hours(orig: dict, d: dict) -> float:
    return round(distance_km(orig["lat"], orig["lon"], d["lat"], d["lon"]) / 760 + 0.6, 1)


def season_mult(d: dict, month: int) -> float:
    c = climate(d, month)
    m = 1.0
    if "beach" in d["tags"]:
        m += 0.25 if c["air"] >= 26 and not c["rainy"] else (-0.15 if c["air"] < 20 else 0)
    if "ski" in d["tags"]:
        m += 0.35 if month in (12, 1, 2, 3) else -0.2
    if month in (12, 1) and d["country"] == "Россия":
        m += 0.1
    return round(m, 2)


def _rng(*parts) -> random.Random:
    seed = int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)
    return random.Random(seed)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M")


def transport_options(orig: dict, d: dict, day: date, direction: str, pets: bool = False) -> list[dict]:
    """Deterministic mock schedule for one direction on one date."""
    out = []
    if orig["id"] == d["id"]:
        return out
    a, b = (orig, d) if direction == "out" else (d, orig)
    dist = distance_km(a["lat"], a["lon"], b["lat"], b["lon"])
    base_h = dist / 760 + 0.6
    sm = season_mult(d, day.month)
    r = _rng(orig["id"], d["id"], day.isoformat(), direction)
    carriers = ["SkyMock Air", "Аэро-Демо", "Polet Mock", "Nordwind-Mock", "Ural Mock"]
    for i in range(4):
        direct = i < 2 or dist < 2500
        stops = 0 if direct else 1
        dur_h = base_h + (0 if direct else r.uniform(2.5, 7))
        dep_h = r.choice([1, 6, 8, 10, 13, 16, 19, 23])
        dep = datetime.combine(day, datetime.min.time()) + timedelta(hours=dep_h, minutes=r.choice([0, 15, 30, 45]))
        arr = dep + timedelta(hours=dur_h)  # times in origin-tz for simplicity (mock)
        price = int(round((3500 + dist * 4.2) * sm * (0.85 if stops else 1.0) * r.uniform(0.75, 1.35), -2))
        out.append(
            dict(
                id=f"F-{a.get('airport', 'X')}{b.get('airport', 'X')}-{day:%m%d}-{i}{direction[0]}",
                mode="flight",
                carrier=r.choice(carriers),
                direction=direction,
                from_name=a["name"],
                to_name=b["name"],
                depart=_fmt(dep),
                arrive=_fmt(arr),
                duration_min=int(dur_h * 60),
                stops=stops,
                price_per_adult=price,
                price_per_child=int(price * 0.75),
                baggage_included=r.random() < 0.6,
                refundable=r.random() < 0.35,
                pets_in_cabin=r.random() < 0.5,
            )
        )
    # trains: domestic & Abkhazia, if not too far
    same_rail = d["country"] in ("Россия", "Абхазия") and dist < 2600 and d["id"] not in ("kaliningrad",)
    if same_rail:
        for i, dep_h in enumerate([r.choice([7, 9, 12]), r.choice([18, 20, 22])]):
            dur_h = dist / 58 + 1
            dep = datetime.combine(day, datetime.min.time()) + timedelta(hours=dep_h, minutes=r.choice([5, 25, 40]))
            arr = dep + timedelta(hours=dur_h)
            price = int(round((1500 + dist * 2.4) * sm * r.uniform(0.85, 1.2), -2))
            out.append(
                dict(
                    id=f"T-{orig['id']}-{d['id']}-{day:%m%d}-{i}{direction[0]}",
                    mode="train",
                    carrier="РЖД-Mock (купе)",
                    direction=direction,
                    from_name=a["name"],
                    to_name=b["name"],
                    depart=_fmt(dep),
                    arrive=_fmt(arr),
                    duration_min=int(dur_h * 60),
                    stops=0,
                    price_per_adult=price,
                    price_per_child=int(price * 0.6),
                    baggage_included=True,
                    refundable=True,
                    pets_in_cabin=True,
                )
            )
    return out
