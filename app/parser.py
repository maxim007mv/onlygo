"""Rule-based NL understanding (offline fallback). Extracts slots from Russian text.
Returns only the slots that were actually mentioned (delta), so it can be merged
into existing session state for incremental re-planning."""
from __future__ import annotations

import re
from datetime import date, timedelta

from . import datastore

MONTHS = {"январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма[йея]": 5, "июн": 6, "июл": 7,
          "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12}
NUM_WORDS = {"один": 1, "одного": 1, "два": 2, "двух": 2, "две": 2, "двое": 2, "три": 3, "трёх": 3, "трех": 3,
             "трое": 3, "четыре": 4, "четверо": 4, "пять": 5, "шесть": 6, "семь": 7, "десять": 10, "неделю": 7}

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


def parse(text: str, today: date | None = None, state: dict | None = None) -> dict:
    today = today or date.today()
    t = " " + text.lower().replace("ё", "е") + " "
    tt = " " + text.lower() + " "
    s: dict = {}
    state = state or {}

    # ---- relative modifications (re-planning) ----
    m = re.search(r"на (\d+|один|два|три|четыре|пять|неделю) (?:дн[яейь]*|день|сут\w*)? ?(позже|раньше)", t)
    if m:
        n = _num(m.group(1)) or 1
        s["shift_days"] = n if m.group(2) == "позже" else -n
    m = re.search(r"на (\d+|один|два|три) (?:дн[яейь]*|день|ноч\w*) (дольше|больше|короче|меньше)", t)
    if m:
        n = _num(m.group(1)) or 1
        s["extend_nights"] = n if m.group(2) in ("дольше", "больше") else -n
    if re.search(r"(бюджет|подешевле|дешевле|меньше денег|бюджет меньше|урез|экономн)", t) and re.search(r"меньш|дешевл|урез|экономн|сократ", t):
        s["budget_factor"] = 0.8
    if re.search(r"(можно|готов\w*) (подороже|дороже)|бюджет больше|увелич\w* бюджет", t):
        s["budget_factor"] = 1.25

    # ---- dates ----
    m = re.search(r"с (\d{1,2})(?:\s*(\w+))? по (\d{1,2}) (\w+)", t)
    month_found = None
    for pat, num in MONTHS.items():
        if re.search(r"\b" + pat, t):
            month_found = num
            break
    if m and month_found:
        d1, d2 = int(m.group(1)), int(m.group(3))
        mo2 = month_found
        mo1 = mo2
        for pat, num in MONTHS.items():
            if m.group(2) and re.match(pat, m.group(2)):
                mo1 = num
        y = today.year if mo1 >= today.month else today.year + 1
        try:
            start = date(y, mo1, d1)
            end = date(y if mo2 >= mo1 else y + 1, mo2, d2)
            s["start_date"], s["nights"] = start.isoformat(), max(1, (end - start).days)
            s["month"] = mo1
        except ValueError:
            pass
    elif month_found:
        s["month"] = month_found
        s["start_date"] = _month_start(month_found, today).isoformat()
    if re.search(r"новый год|новогодн", t):
        y = today.year
        s["start_date"], s["month"] = date(y, 12, 29).isoformat(), 12
        s.setdefault("nights", 7)
    if re.search(r"на (эти |ближайшие )?выходн", t):
        d = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
        s["start_date"], s["nights"], s["month"] = d.isoformat(), 2, d.month
    if re.search(r"(в ближайш|скоро|на следующей неделе|через неделю|как можно скорее)", t):
        d = today + timedelta(days=7)
        s["start_date"], s["month"] = d.isoformat(), d.month
    m = re.search(r"на (\d+|одну|две|три|неделю|две недели|пару) ?(недел\w*|дн[яейь]*|ноч\w*|сут\w*)?", t)
    if m and "shift_days" not in s and "extend_nights" not in s:
        raw, unit = m.group(1), (m.group(2) or "")
        if raw == "неделю":
            s["nights"] = 7
        elif raw == "две недели" or (raw in ("две", "2") and unit.startswith("недел")):
            s["nights"] = 14
        elif unit.startswith("недел"):
            s["nights"] = 7 * (_num(raw) or 1)
        elif unit.startswith(("дн", "ноч", "сут")):
            n = _num(raw) if raw != "пару" else 2
            if n:
                s["nights"] = n if unit.startswith("ноч") else max(1, n - 1)
    if re.search(r"\b(не знаю|без разницы|все равно|любые даты|неважно|не важно)\b", t) and not state.get("start_date") and "start_date" not in s:
        s["dates_flexible"] = True

    # ---- budget ----
    m = re.search(r"(\d[\d\s]*[.,]?\d*)\s*(тыс\w*|т\.р|к\b|k\b|000|руб|₽|р\b)", t)
    if m and re.search(r"бюджет|до |за |не дороже|в пределах|рубл|₽|тыс", t):
        num = float(m.group(1).replace(" ", "").replace(",", "."))
        unit = m.group(2)
        val = num * 1000 if unit.startswith(("тыс", "т.р", "к", "k")) else num
        if unit == "000":
            val = num * 1000
        if 5000 <= val <= 5_000_000:
            s["budget"] = int(val)
    if re.search(r"недорог|дешев|бюджетн|эконом|подешевле", t) and "budget_factor" not in s:
        s["price_sensitive"] = True

    # ---- guests ----
    if re.search(r"с ребенком|с ребёнком|с сыном|с дочк|с дочерью|с малыш", tt.replace("ё", "е")):
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
        s.setdefault("children", max(len(ages), state.get("children") or 0) or len(ages))
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

    # ---- constraints ----
    if re.search(r"без долг\w* перел|недалеко|недолг\w* перел|коротк\w* перел|не больше (\d+) час|близко", t):
        mh = re.search(r"не (?:больше|дольше|более) (\d+) ?час", t)
        s["max_travel_hours"] = int(mh.group(1)) if mh else 5
    mh = re.search(r"перел[её]т\w* до (\d+) ?час", t)
    if mh:
        s["max_travel_hours"] = int(mh.group(1))
    if re.search(r"без пересад|прям\w* рейс", t):
        s["direct_only"] = True
    if re.search(r"без виз|безвиз", t):
        s["visa_free_only"] = True
    if re.search(r"на поезде|поездом|не люблю летать|боюсь летать|без самол", t):
        s["prefer_train"] = True
    if re.search(r"все включено|всё включено|олл ?инклюзив|all ?inclusive", t):
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

    # ---- origin ----
    m = re.search(r"\bиз ([а-яё\-]+)", t)
    if m:
        for o in datastore.origins():
            if any(m.group(1).startswith(a) for a in o["aliases"]):
                s["origin"] = o["id"]

    # ---- moods ----
    moods = [k for k, p in MOODS.items() if re.search(p, t)]
    if moods:
        s["moods"] = moods

    # ---- explicit destination ----
    countries = {d["country"] for d in datastore.destinations() if d["country"] != "Россия"}
    for cn in countries:
        stem = cn.lower()[:-1] if len(cn) > 4 else cn.lower()
        if re.search(r"\b" + re.escape(stem), t):
            s["country"] = cn
    for d in datastore.destinations():
        key = d["name"].lower().replace("ё", "е").split(" (")[0]
        stem = key[:-1] if len(key) > 5 else key
        if re.search(r"\b" + re.escape(stem), t) or re.search(re.escape(d["country"].lower()[:-1]), t) and len(d["country"]) > 4:
            if re.search(r"не (хочу |надо )?(в |на )?" + re.escape(stem), t) or re.search(r"кроме " + re.escape(stem), t):
                s.setdefault("exclude_destinations", []).append(d["id"])
            elif re.search(r"\b" + re.escape(stem), t):
                s["destination"] = d["id"]
                break
    if re.search(r"другое направление|другую страну|не нравится направление|куда-нибудь еще", t):
        s["change_destination"] = True
    if re.search(r"другой отель|другую гостиниц|не нравится отель", t):
        s["change_hotel"] = True
    return s
