"""Regression tests for phrasings found in a pass over unseen jury-style queries."""

from datetime import date

from app import parser

TODAY = date(2026, 10, 28)


def test_single_day_month_date_and_nights_without_na():
    d = parser.parse("Тбилиси 5 ночей с 10 декабря", TODAY)
    assert d["start_date"] == "2026-12-10"
    assert d["nights"] == 5
    assert d["destination"] == "tbilisi"


def test_alias_at_sentence_start_and_may_holidays():
    d = parser.parse("Питер на майские вдвоём, бюджет 60к", TODAY)
    assert d["destination"] == "spb"
    assert d["start_date"] == "2027-05-01"
    assert d["nights"] == 4
    assert d["budget"] == 60000


def test_train_word_sets_preference():
    d = parser.parse("поезд в Казань на 3 дня", TODAY)
    assert d["prefer_train"] is True
    assert d["destination"] == "kazan"
    assert d["nights"] == 2


def test_word_range_still_wins_over_single_date():
    d = parser.parse("с 3 по 10 ноября", TODAY)
    assert d["start_date"] == "2026-11-03" and d["nights"] == 7


def test_child_age_is_not_a_date():
    d = parser.parse("2 взрослых и ребёнок 10 лет", TODAY)
    assert "start_date" not in d
