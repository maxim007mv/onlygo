"""Reply wording: Russian plurals and honest handling of places outside the demo catalogue."""

from datetime import date

from app import agent

TODAY = date(2026, 10, 28)


def test_plural_ru():
    forms = ("вопрос", "вопроса", "вопросов")
    assert [agent.plural_ru(n, *forms) for n in (1, 2, 4, 5, 11, 12, 21, 22, 25, 111)] == [
        "вопрос",
        "вопроса",
        "вопроса",
        "вопросов",
        "вопросов",
        "вопросов",
        "вопрос",
        "вопроса",
        "вопросов",
        "вопросов",
    ]


def test_ask_phrase_matches_count():
    assert agent._ask_phrase(1) == "уточню всего одно:"
    assert agent._ask_phrase(2) == "уточню 2 вопроса:"
    assert agent._ask_phrase(5) == "уточню 5 вопросов:"


def test_unknown_place_is_explained_even_when_asking():
    r = agent.handle_message(agent.new_state(), "хочу на Бали", {}, TODAY)
    assert r["reply"].startswith("Бали пока нет в демо-каталоге")
    assert "ближе всего" in r["reply"]


def test_unknown_place_with_dates_builds_closest_plan_honestly():
    r = agent.handle_message(agent.new_state(), "хочу на Бали в декабре на неделю", {}, TODAY)
    assert r["plan"] is not None
    assert "Бали пока нет в демо-каталоге" in r["reply"]
    assert r["plan"]["destination"]["country"] != "Индонезия"
