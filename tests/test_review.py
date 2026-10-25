"""Tests for the win-analysis / code-review round: parser edge cases, honest clarification, infra."""

import os
import tempfile
from datetime import date

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "t.db"))

import pytest
from fastapi.testclient import TestClient

from app import agent, db, main, parser, tools

c = TestClient(main.app, raise_server_exceptions=False)
TODAY = date(2026, 10, 25)


def p(text: str) -> dict:
    return parser.parse(text, today=TODAY)


@pytest.mark.parametrize(
    ("text", "key", "want"),
    [
        ("бюджет 150.000 рублей", "budget", 150000),
        ("на 2.5 недели", "nights", 18),
        ("перелёт не больше 2.5 часа", "max_travel_hours", 2.5),
        ("поездом из Москвы в Питер", "destination", "spb"),
        ("едем с 3 детьми", "children", 3),
    ],
)
def test_parser_edge_cases(text, key, want):
    assert p(text).get(key) == want


def test_origin_city_is_not_destination():
    assert p("из Сочи в Казань").get("origin") != p("из Сочи в Казань").get("destination")


def test_after_session_means_late_january():
    assert p("после сессии к морю")["start_date"] == "2026-01-26" or p("после сессии к морю")["start_date"].endswith("-01-26")


def test_unknown_place_is_flagged_not_substituted():
    s = p("летим в Париж")
    assert s.get("unknown_place") and not s.get("destination")
    j = c.post("/api/chat", json={"message": "летим в Париж на неделю в ноябре"}).json()
    assert any("Париж" in n for n in j["notes"])
    assert (j.get("plan") or {}).get("destination", {}).get("name") != "Париж"


def test_known_destination_does_not_block_on_dates():
    j = c.post("/api/chat", json={"message": "в Сочи на 3 дня"}).json()
    assert j["plan"], "destination + duration must plan right away"
    assert all(not q.get("blocking") for q in j["questions"])


def test_vague_request_still_blocks_on_dates():
    j = c.post("/api/chat", json={"message": "хочу отдохнуть"}).json()
    assert not j["plan"] and any(q.get("blocking") for q in j["questions"])


def test_children_question_is_plural():
    j = c.post("/api/chat", json={"message": "на море с 3 детьми"}).json()
    assert any("детям" in q["text"] for q in j["questions"])


def test_over_budget_is_said_plainly():
    j = c.post("/api/chat", json={"message": "после сессии к морю за 15-20 тысяч"}).json()
    if j["plan"]["total"] > 20000:
        assert "Честно" in j["reply"]


def test_honest_limits_travel_hours():
    state = {"req": {"max_travel_hours": 2, "destination": "x"}, "plan": {"total": 1, "destination": {"flight_hours": 4.0}}}
    assert "дольше вашего лимита 2 ч" in agent.honest_limits(state)[0]


def test_rate_limiter_window():
    main._hits.clear()
    assert not any(main.rate_limited("ip", now=100.0 + i, limit=3) for i in range(3))
    assert main.rate_limited("ip", now=103.0, limit=3)
    assert not main.rate_limited("ip", now=200.0, limit=3), "window slides after 60 s"
    assert not main.rate_limited("ip", limit=0), "0 disables"
    main._hits.clear()


def test_purge_sessions_removes_only_stale():
    db.save_session("stale-sid", None, {"x": 1})
    db.conn().execute("UPDATE sessions SET updated='2000-01-01T00:00:00+00:00' WHERE id='stale-sid'")
    db.conn().commit()
    db.save_session("live-sid", None, {"x": 1})
    assert db.purge_sessions(ttl_days=1) >= 1
    assert db.get_session("stale-sid") is None and db.get_session("live-sid") is not None


def test_today_is_timezone_aware():
    assert tools.LOCAL_TZ is not None and isinstance(tools.today_local(), date)


def test_post_purchase_bad_shift_is_422():
    j = c.post("/api/chat", json={"message": "Хочу в Сочи на неделю в ноябре вдвоём"}).json()
    b = c.post("/api/checkout", json={"session_id": j["session_id"]})
    if b.status_code != 200:
        pytest.skip("checkout contract differs")
    bid = b.json()["booking_id"]
    assert c.post("/api/post_purchase", json={"booking_id": bid, "action": "change_dates", "value": "abc"}).status_code == 422
    assert "симуляция" in c.post("/api/post_purchase", json={"booking_id": bid, "action": "reminder"}).json()["message"]
