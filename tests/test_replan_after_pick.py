"""Replans after the user hand-picked a destination must never drop the plan."""

from datetime import date

import pytest

from app import agent

TODAY = date(2026, 10, 28)
FIRST = "хочу куда-нибудь к тёплому морю в ноябре, до 150 тысяч, с ребёнком 5 лет, без долгих перелётов"


def _picked(did: str = "rak") -> dict:
    st = agent.new_state()
    agent.handle_message(st, FIRST, {}, TODAY)
    agent.select(st, "destination", did)
    agent.handle_message(st, "на 2 дня позже", {}, TODAY)
    assert st["plan"]["destination"]["id"] == did
    return st


def test_dog_after_pick_keeps_plan_and_offers_alternatives():
    st = _picked()
    r = agent.handle_message(st, "мы поедем с собакой", {}, TODAY)
    p = r["plan"]
    assert p is not None
    assert p["destination"]["id"] == "rak"  # user's pick is respected, not silently switched
    assert "Не смог собрать" not in r["reply"]
    unmet = next(c for c in p["checks"] if c["title"] == "Условие не выполнено")
    assert "животных" in unmet["text"]
    fix = unmet["fix"]
    assert fix["kind"] == "destination" and fix["id"] != "rak"
    # one click on the offered alternative gives a pet-friendly plan
    r2 = agent.select(st, "destination", fix["id"])
    assert r2["plan"]["destination"]["id"] == fix["id"]
    hotel = next(i for i in r2["plan"]["items"] if i["kind"] == "hotel")
    assert "Условие не выполнено" not in [c["title"] for c in r2["plan"]["checks"]]
    assert hotel


@pytest.mark.parametrize("msg", ["с нами ещё двое детей 5 и 8 лет", "давай подешевле", "бюджет до 60 тысяч", "хочу на поезде"])
def test_other_replans_after_pick_keep_destination(msg):
    st = _picked()
    r = agent.handle_message(st, msg, {}, TODAY)
    assert r["plan"] is not None, msg
    assert r["plan"]["destination"]["id"] == "rak"
    assert "Не смог собрать" not in r["reply"]


def test_pinned_destination_card_is_the_pinned_one():
    st = _picked()
    agent.handle_message(st, "мы поедем с собакой", {}, TODAY)
    assert st["candidates"]["destinations"][0]["id"] == "rak"


def test_train_after_pick_explains_no_train():
    st = _picked()
    r = agent.handle_message(st, "хочу на поезде", {}, TODAY)
    assert any("Поезда до" in c for c in r["changes"])
