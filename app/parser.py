"""Rule-based NL understanding (offline fallback). Extracts slots from Russian text.
Returns only the slots that were actually mentioned (delta), so it can be merged
into existing session state for incremental re-planning."""

from __future__ import annotations

import re
from datetime import date, timedelta

from . import datastore
from .tools import today_local

MONTHS = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "ма[йея]": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}
NUM_WORDS = {
    "один": 1,
    "одного": 1,
    "два": 2,
    "двух": 2,
    "две": 2,
    "двое": 2,
    "три": 3,
    "трёх": 3,
    "трех": 3,
    "трое": 3,
    "четыре": 4,
    "четверо": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "десять": 10,
    "неделю": 7,
}

MOODS = {
    "warm": r"тёпл|тепл|жарк|солнц|лето зимой",
    "sea": r"мор|океан|пляж|купат|остров",
    "beach": r"пляж|купат|загар",
    "mountains": r"гор[аыу ]|горы|горн",
    "ski": r"лыж|сноуборд|горнолыж",
    "city": r"город|экскурс|музе|прогул",
    "food": r"гастро|ед[аы]|кухн|вино",
    "calm": r"спокойн|тих|релакс|отдохнуть от|без толп",
    "party": r"тусов|вечерин|ночн[аы]я жизнь|клуб",
    "romantic": r"романт|медовый|годовщин|с девушкой|с парнем|вдвоём с",
    "nature": r"природ|поход|треккинг|озер",
    "luxury": r"роскош|люкс|премиум|5 ?звезд",
    "diving": r"дайвинг|снорклинг|рифы|кораллы",
}


def _num(s: str) -> int | None:
    s = s.strip().lower()
    if s.isdigit():
        return int(s)
    return NUM_WORDS.get(s)


def _month_start(month: int, today: date) -> date:
    if month == today.month:  # "в этом месяце" -> soon, not next year
        return today + timedelta(days=5)
    year = today.year if month > today.month else today.year + 1
    d = date(year, month, 10)
    while d.weekday() != 5:  # Saturday
        d += timedelta(days=1)
    if d <= today:
        d = today + timedelta(days=7)
    return d


MAX_NIGHTS = 30
MAX_ADULTS = 9
MAX_CHILDREN = 6
MIN_BUDGET, MAX_BUDGET = 5_000, 5_000_000


def _future(d: date, today: date) -> date:
    """Dates without a year mean the nearest future occurrence."""
    while d < today:
        d = d.replace(year=d.year + 1)
    return d


def _safe_date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def sanitize(delta: dict, today: date) -> dict:
    """Validate/clamp a slot delta (from rules OR from an LLM) so planning never sees bad types/values."""
    out: dict = {}
    for k, v in (delta or {}).items():
        try:
            if k == "start_date":
                d = date.fromisoformat(str(v)[:10])
                if d < today or d > today + timedelta(days=550):
                    continue
                out[k] = d.isoformat()
            elif k == "month":
                if 1 <= int(v) <= 12:
                    out[k] = int(v)
            elif k == "nights":
                out[k] = min(MAX_NIGHTS, max(1, int(v)))
            elif k == "adults":
                out[k] = min(MAX_ADULTS, max(1, int(v)))
            elif k == "children":
                out[k] = min(MAX_CHILDREN, max(0, int(v)))
            elif k == "child_ages":
                out[k] = [int(a) for a in v if 0 <= int(a) < 18][:MAX_CHILDREN]
            elif k == "budget":
                if MIN_BUDGET <= float(v) <= MAX_BUDGET:
                    out[k] = int(v)
            elif k in ("shift_days", "extend_nights"):
                if int(v):
                    out[k] = max(-60, min(60, int(v)))
            elif k == "budget_factor":
                if 0.3 <= float(v) <= 3:
                    out[k] = float(v)
            elif k == "max_travel_hours":
                if 1 <= float(v) <= 30:
                    out[k] = round(float(v) * 2) / 2
            elif k == "meals":
                if v in ("RO", "BB", "HB", "AI"):
                    out[k] = v
            elif k in ("destination",):
                if datastore.destination(str(v)):
                    out[k] = str(v)
            elif k == "exclude_destinations":
                out[k] = [x for x in v if datastore.destination(str(x))]
            elif k == "origin":
                if any(o["id"] == v for o in datastore.origins()):
                    out[k] = v
            elif k == "moods":
                out[k] = [m for m in v if m in MOODS]
            elif k in ("country",):
                if isinstance(v, str) and v:
                    out[k] = v
            elif k == "unknown_place":
                if isinstance(v, str) and v:
                    out[k] = v[:40]
            elif k == "implicit_notes":
                out[k] = [str(x)[:200] for x in v][:5]
            elif isinstance(v, bool):
                out[k] = v
        except (TypeError, ValueError):
            continue
    if out.get("child_ages") and "children" not in out:
        out["children"] = len(out["child_ages"])
    return out


# Popular places the user may name that are NOT in the demo catalogue: answer honestly instead of silently
# substituting another city. Stem -> display name.
UNKNOWN_PLACES = {
    "париж": "Париж",
    "рим": "Рим",
    "лондон": "Лондон",
    "барселон": "Барселона",
    "праг": "Прага",
    "берлин": "Берлин",
    "мадрид": "Мадрид",
    "милан": "Милан",
    "вену": "Вена",
    "амстердам": "Амстердам",
    "нью-йорк": "Нью-Йорк",
    "токио": "Токио",
    "бали": "Бали",
    "сеул": "Сеул",
    "пекин": "Пекин",
    "баку": "Баку",
    "минск": "Минск",
    "ташкент": "Ташкент",
    "самарканд": "Самарканд",
    "владивосток": "Владивосток",
    "байкал": "Байкал",
    "мурманск": "Мурманск",
    "камчатк": "Камчатка",
}
DEST_ALIASES = {"spb": ("питер", "спб", "петербург"), "rosa": ("роза хутор", "красную поляну"), "limassol": ("кипр",), "gagra": ("абхази",)}
TO = r"(?:\bв|\bво|\bдо|\bна)\s+"
NUM = r"\d+(?:[.,]\d+)?"
WORD_NUMS = "один|одну|два|две|три|четыре|пять|неделю|пару"
DEFAULT_SHORT_HOURS = 5  # «без долгих перелётов» without a number


def _to_float(s: str) -> float:
    return float(s.replace(",", "."))


def parse(text: str, today: date | None = None, state: dict | None = None) -> dict:
    """Extract the slots mentioned in `text` (a delta to merge into session state)."""
    today = today or today_local()
    state = state or {}
    t = " " + text.lower().replace("ё", "е") + " "
    s: dict = {}
    _parse_replan(t, s)
    _parse_dates(t, today, s)
    _parse_duration(t, s)
    if (
        re.search(r"\b(не знаю|без разницы|все равно|любые даты|неважно|не важно)\b", t)
        and not state.get("start_date")
        and "start_date" not in s
    ):
        s["dates_flexible"] = True
    _parse_budget(t, s)
    _parse_guests(t, s, state)
    _parse_constraints(t, s)
    _parse_origin(t, s)
    moods = [k for k, p in MOODS.items() if re.search(p, t)]
    if moods:
        s["moods"] = moods
    _parse_destination(t, s)
    return s


def _parse_replan(t: str, s: dict) -> None:
    m = re.search(r"на (\d+|один|два|три|четыре|пять|неделю) (?:дн[яейь]*|день|сут\w*)? ?(позже|раньше)", t)
    if m:
        n = _num(m.group(1)) or 1
        s["shift_days"] = n if m.group(2) == "позже" else -n
    m = re.search(r"на (\d+|один|два|три) (?:дн[яейь]*|день|ноч\w*) (дольше|больше|короче|меньше)", t)
    if m:
        n = _num(m.group(1)) or 1
        s["extend_nights"] = n if m.group(2) in ("дольше", "больше") else -n
    if re.search(r"(бюджет|подешевле|дешевле|меньше денег|урез|экономн)", t) and re.search(r"меньш|дешевл|урез|экономн|сократ", t):
        s["budget_factor"] = 0.8
    if re.search(r"(можно|готов\w*) (подороже|дороже)|бюджет больше|увелич\w* бюджет", t):
        s["budget_factor"] = 1.25
    if re.search(r"другое направление|другую страну|не нравится направление|куда-нибудь еще", t):
        s["change_destination"] = True
    if re.search(r"другой отель|другую гостиниц|не нравится отель", t):
        s["change_hotel"] = True


def _find_month(t: str) -> int | None:
    return next((num for pat, num in MONTHS.items() if re.search(r"\b" + pat, t)), None)


def _parse_dates(t: str, today: date, s: dict) -> None:
    month = _find_month(t)
    found = bool(month) and (_parse_word_range(t, month, today, s) or _parse_day_month(t, today, s))
    if not found and not _parse_numeric_date(t, today, s) and month:
        s["month"], s["start_date"] = month, _month_start(month, today).isoformat()
    _parse_named_dates(t, today, s)


def _parse_word_range(t: str, month: int, today: date, s: dict) -> bool:
    """«с 3 по 10 ноября», «с 28 декабря по 5 января»."""
    m = re.search(r"с (\d{1,2})(?:\s*(\w+))? по (\d{1,2}) (\w+)", t)
    if not m:
        return False
    mo1 = next((num for pat, num in MONTHS.items() if m.group(2) and re.match(pat, m.group(2))), month)
    start = _safe_date(today.year, mo1, int(m.group(1)))
    if not start:
        return False
    start = _future(start, today)
    end = _safe_date(start.year, month, int(m.group(3)))
    if end and end <= start:
        end = _safe_date(start.year + 1, month, int(m.group(3)))
    if not end:
        return False
    s["start_date"], s["nights"], s["month"] = start.isoformat(), min(MAX_NIGHTS, max(1, (end - start).days)), mo1
    return True


def _parse_day_month(t: str, today: date, s: dict) -> bool:
    """«с 10 декабря», «10 января» (single date written with a month word)."""
    for m in re.finditer(r"\b(\d{1,2}) (\w+)", t):
        mo = next((num for pat, num in MONTHS.items() if re.match(pat, m.group(2))), None)
        start = mo and _safe_date(today.year, mo, int(m.group(1)))
        if start:
            start = _future(start, today)
            s["start_date"], s["month"] = start.isoformat(), start.month
            return True
    return False


def _parse_numeric_date(t: str, today: date, s: dict) -> bool:
    """«10.12», «3.01.2027», «с 10.12 по 15.12» — but not «2.5 часа» / «150.000 ₽»."""
    num = re.search(
        r"(?:с\s*)?(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?(?:\s*(?:по|-|–|до)\s*(\d{1,2})\.(\d{1,2})(?:\.\d{2,4})?)?"
        r"(?![\d.])(?!\s*(?:ч|час|недел|дн|тыс|млн|к\b|%|руб|₽))",
        t,
    )
    start = num and _safe_date(today.year, int(num.group(2)), int(num.group(1)))
    if not start:
        return False
    if num.group(3):
        yy = int(num.group(3))
        start = _safe_date(yy + 2000 if yy < 100 else yy, start.month, start.day) or start
    start = _future(start, today)
    s["start_date"], s["month"] = start.isoformat(), start.month
    if num.group(4):
        end = _safe_date(start.year, int(num.group(5)), int(num.group(4)))
        if end and end <= start:
            end = _safe_date(start.year + 1, end.month, end.day)
        if end:
            s["nights"] = min(MAX_NIGHTS, max(1, (end - start).days))
    return True


def _parse_named_dates(t: str, today: date, s: dict) -> None:
    if re.search(r"новый год|новогодн", t):
        ny = _future(date(today.year, 12, 29), today)
        s["start_date"], s["month"] = ny.isoformat(), 12
        s.setdefault("nights", 7)
    if re.search(r"\bмайск", t) and "nights" not in s:
        d = _future(date(today.year, 5, 1), today)
        s["start_date"], s["month"], s["nights"] = d.isoformat(), 5, 4
    if re.search(r"на (эти |ближайшие )?выходн", t):
        d = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
        s["start_date"], s["nights"], s["month"] = d.isoformat(), 2, d.month
    if re.search(r"(в ближайш|скоро|на следующей неделе|через неделю|как можно скорее)", t):
        d = today + timedelta(days=7)
        s["start_date"], s["month"] = d.isoformat(), d.month
    if re.search(r"после сесси", t) and "start_date" not in s:
        # winter exams end ~25 Jan, summer ones ~30 Jun
        d = _future(date(today.year, 1, 26), today) if today.month >= 9 or today.month == 1 else _future(date(today.year, 7, 1), today)
        s["start_date"], s["month"] = d.isoformat(), d.month


def _parse_duration(t: str, s: dict) -> None:
    if "shift_days" in s or "extend_nights" in s:
        return
    m = re.search(rf"на ({NUM}|{WORD_NUMS}|две недели) ?(недел\w*|дн[яейь]*|ноч\w*|сут\w*)?", t) or re.search(
        rf"\b({NUM}|{WORD_NUMS}) (ноч\w*|недел\w*)", t
    )
    if not m:
        return
    raw, unit = m.group(1), (m.group(2) or "")
    if raw == "неделю":
        s["nights"] = 7
    elif raw == "две недели":
        s["nights"] = 14
    elif unit.startswith("недел"):
        n = _to_float(raw) if re.fullmatch(NUM, raw) else (_num(raw) or 1)
        s["nights"] = min(MAX_NIGHTS, round(7 * n))
    elif unit.startswith(("дн", "ноч", "сут")):
        n = 2 if raw == "пару" else (round(_to_float(raw)) if re.fullmatch(NUM, raw) else _num(raw))
        if n:
            s["nights"] = n if unit.startswith("ноч") else max(1, n - 1)


def _parse_budget(t: str, s: dict) -> None:
    # «150.000» / «150 000» are thousands separators, not decimals
    tb = re.sub(r"(?<=\d)[ .](?=\d{3}(?!\d))", "", t)
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(тыс\w*|т\.р|к\b|k\b|000|руб|₽|р\b)", tb)
    if m and re.search(r"бюджет|до |за |не дороже|в пределах|рубл|₽|тыс", tb):
        num, unit = _to_float(m.group(1)), m.group(2)
        val = num * 1000 if unit.startswith(("тыс", "т.р", "к", "k", "000")) else num
        if MIN_BUDGET <= val <= MAX_BUDGET:
            s["budget"] = int(val)
    if re.search(r"недорог|дешев|бюджетн|эконом|подешевле", t) and "budget_factor" not in s:
        s["price_sensitive"] = True


def _parse_guests(t: str, s: dict, state: dict) -> None:
    if re.search(r"с ребенком|с сыном|с дочк|с дочерью|с малыш", t):
        s["children"] = 1
    m = re.search(r"(\d|двое|двумя|трое|тремя|два|двух|три|трех) (?:детьми|детей|ребенка|ребят)", t)
    if m:
        s["children"] = {"двумя": 2, "тремя": 3}.get(m.group(1)) or _num(m.group(1)) or 2
    elif re.search(r"с детьми", t):
        s["children"] = 2
    ages = []
    for grp in re.findall(r"(\d{1,2}(?:\s*(?:,|и)\s*\d{1,2})*)\s*(?:лет|год|года)\b", t):
        ages += [int(x) for x in re.findall(r"\d{1,2}", grp) if int(x) < 18]
    if ages:
        s["child_ages"] = ages
        s.setdefault("children", max(len(ages), state.get("children") or 0))
    if re.search(r"\bбез (ребенка|детей)\b", t):
        s["children"], s["child_ages"] = 0, []
    if re.search(r"вдвоем|с женой|с мужем|с девушкой|с парнем|с подругой|с другом|пара\b|для двоих", t):
        s["adults"] = 2
    m = re.search(r"(\d|двое|трое|четверо) (?:взрослых|человек)", t)
    if m:
        s["adults"] = _num(m.group(1)) or 2
    if re.search(r"\b(один|одна|соло|в одиночку)\b(?! из)", t) and "adults" not in s and not re.search(r"один (день|раз)", t):
        s["adults"] = 1
    if re.search(r"собак|пес |псом|щен|кошк|кот[оа ]|питом|животн", t):
        s["pets"] = not re.search(r"без (собак|кошк|питом|животн)", t)


def _parse_constraints(t: str, s: dict) -> None:
    mh = re.search(rf"(?:не (?:больше|дольше|более)|до|максимум) ({NUM}) ?час", t)
    if mh and re.search(r"перел|дорог|лет[еи]|в пути|не (больше|дольше|более)", t):
        s["max_travel_hours"] = _to_float(mh.group(1))
    elif re.search(r"без долг\w* перел|недалеко|недолг\w* перел|коротк\w* перел|близко", t):
        s["max_travel_hours"] = DEFAULT_SHORT_HOURS
    if re.search(r"без пересад|прям\w* рейс", t):
        s["direct_only"] = True
    if re.search(r"без виз|безвиз", t):
        s["visa_free_only"] = True
    if re.search(r"на поезде|поездом|\bпоезд(?:ом)? (?:в|до)\b|не люблю летать|боюсь летать|без самол", t):
        s["prefer_train"] = True
    if re.search(r"все включено|олл ?инклюзив|all ?inclusive", t):
        s["meals"] = "AI"
    elif re.search(r"с завтрак", t):
        s["meals"] = "BB"
    elif re.search(r"полупансион", t):
        s["meals"] = "HB"
    if re.search(r"бесплатн\w* отмен|возвратн|отменить|могут поменяться планы", t):
        s["flexible_cancellation"] = True
    if re.search(r"первая линия|у моря|рядом с пляжем|близко к пляжу|первой линии", t):
        s["beach_close"] = True
    if re.search(r"в центре|центр города", t):
        s["center_close"] = True


def _parse_origin(t: str, s: dict) -> None:
    m = re.search(r"\bиз ([а-я\-]+)", t)
    if not m:
        return
    for o in datastore.origins():
        if any(m.group(1).startswith(a) for a in o["aliases"]):
            s["origin"] = o["id"]


def _parse_destination(t: str, s: dict) -> None:
    countries = {d["country"] for d in datastore.destinations() if d["country"] != "Россия"}
    for cn in countries:
        stem = cn.lower()[:-1] if len(cn) > 4 else cn.lower()
        if re.search(r"\b" + re.escape(stem), t):
            s["country"] = cn
    for did, aliases in DEST_ALIASES.items():
        if any(re.search(r"(?:" + TO + r"|^\s*)" + re.escape(a), t) for a in aliases):
            s["destination"] = did
            return
    for d in datastore.destinations():
        key = d["name"].lower().replace("ё", "е").split(" (")[0]
        stem = re.escape(key[:-1] if len(key) > 5 else key)
        if not re.search(r"\b" + stem, t):
            continue
        if re.search(r"не (хочу |надо )?(в |на )?" + stem, t) or re.search(r"кроме " + stem, t):
            s.setdefault("exclude_destinations", []).append(d["id"])
        elif not re.search(r"\bиз " + stem, t):
            s["destination"] = d["id"]
            return
    unknown = next((name for stem, name in UNKNOWN_PLACES.items() if re.search(TO + re.escape(stem), t)), None)
    if unknown:
        s["unknown_place"] = unknown
