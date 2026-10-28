"""Broader jury-style queries: dates, groups, accessibility, infants, elderly, multi-city."""

from datetime import date

from app import agent, parser

TODAY = date(2026, 10, 28)


def _run(q: str):
    st = agent.new_state()
    r = agent.handle_message(st, q, {}, TODAY)
    return st, r


def test_dash_date_range():
    s = parser.parse("в Казань 20-25 ноября", TODAY)
    assert s["start_date"] == "2026-11-20" and s["nights"] == 5
    s = parser.parse("28 декабря – 4 января", TODAY)
    assert s["start_date"] == "2026-12-28" and s["nights"] == 7


def test_relative_days():
    assert parser.parse("на завтра на 3 дня", TODAY)["start_date"] == "2026-10-29"
    assert parser.parse("послезавтра в Сочи", TODAY)["start_date"] == "2026-10-30"


def test_infant_counts_as_child_age_zero():
    s = parser.parse("на море с младенцем 6 месяцев", TODAY)
    assert s["children"] == 1 and s["child_ages"] == [0]


def test_elderly_prefers_short_trip():
    st, r = _run("с бабушкой 80 лет, тепло, февраль")
    assert st["req"]["max_travel_hours"] <= 5
    assert r["plan"]["destination"]["flight_hours"] <= 5.5


def test_accessibility_is_honest():
    _, r = _run("отель с доступной средой для колясочника, Анталья, в декабре")
    assert "Доступная среда" in r["reply"]


def test_multi_city_is_honest():
    _, r = _run("хочу сначала в Казань потом в Питер в ноябре на неделю")
    assert r["plan"] and "нескольких городов" in r["reply"]


def test_twenty_queries_never_crash():
    qs = [
        "хочу отдохнуть",
        "на море с 15.12 по 22.12 вдвоём до 200к",
        "в Сочи на выходные",
        "компания 6 человек, Калининград, 5 ночей в ноябре",
        "с собакой на неделю к морю, бюджет 120 тыс",
        "горящий тур на завтра на 3 дня",
        "недорого в Европу без визы",
        "на майские в Турцию",
        "1 ночь в Питере 30 ноября по работе",
        "отпуск на 2 недели в марте, всё включено",
        "бюджет 30 тысяч на двоих на неделю на море",
        "в горы покататься на лыжах в январе",
        "хочу на Мальдивы",
        "с кошкой и двумя детьми в Сочи на новый год",
    ]
    for q in qs:
        _, r = _run(q)
        assert r["reply"] and (r["plan"] or r["questions"]), q
