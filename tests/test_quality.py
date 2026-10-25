"""Regression tests for the code-quality audit (see CODE_AUDIT.md)."""
import os
import tempfile
import threading
from datetime import date

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "t.db"))

from fastapi.testclient import TestClient  # noqa: E402

from app import agent, parser, tools  # noqa: E402
from app.main import app  # noqa: E402

c = TestClient(app, raise_server_exceptions=False)
TODAY = date(2026, 10, 25)


def _plan(msg="Хочу в Сочи на неделю в ноябре вдвоём"):
    r = c.post("/api/chat", json={"message": msg})
    assert r.status_code == 200
    return r.json()


# ---- API never 500s on bad input
def test_select_unknown_ids_are_4xx():
    sid = _plan()["session_id"]
    assert c.post("/api/select", json={"session_id": sid, "kind": "hotel", "id": "nope"}).status_code == 400
    assert c.post("/api/select", json={"session_id": sid, "kind": "bogus", "id": "x"}).status_code == 422
    assert c.post("/api/select", json={"session_id": sid, "kind": "destination", "id": "atlantis"}).status_code == 400
    assert c.post("/api/select", json={"session_id": "missing", "kind": "hotel", "id": "x"}).status_code == 404


def test_select_valid_candidate_still_works():
    r = _plan()
    alt = r["candidates"]["hotels"][-1]["id"] if "candidates" in r else None
    if alt:
        r2 = c.post("/api/select", json={"session_id": r["session_id"], "kind": "hotel", "id": alt})
        assert r2.status_code == 200 and r2.json()["plan"]


def test_bad_payloads():
    assert c.post("/api/chat", json={"message": "   "}).status_code == 400
    assert c.post("/api/chat", json={"message": "x", "session_id": "../../etc"}).status_code == 422
    assert c.post("/api/chat", content=b"{not json", headers={"content-type": "application/json"}).status_code == 422
    assert c.post("/api/correct", json={"session_id": _plan()["session_id"], "slot": "nonexistent"}).status_code == 400
    assert c.post("/api/post_purchase", json={"booking_id": "OG-X", "action": "hack"}).status_code == 422


def test_post_purchase_value_validation_and_double_checkout():
    sid = _plan()["session_id"]
    a = c.post("/api/checkout", json={"session_id": sid}).json()
    b = c.post("/api/checkout", json={"session_id": sid}).json()
    assert a["status"] == "paid" and b["booking_id"] == a["booking_id"] and b["duplicate"]
    bid = a["booking_id"]
    assert c.post("/api/post_purchase", json={"booking_id": bid, "action": "change_dates", "value": "abc"}).status_code == 422
    assert c.post("/api/post_purchase", json={"booking_id": bid, "action": "change_dates", "value": "999"}).status_code == 422
    assert c.post("/api/post_purchase", json={"booking_id": bid, "action": "change_dates", "value": "2"}).status_code == 200


def test_booking_keeps_user_id():
    from app import db
    r = c.post("/api/chat", json={"message": "Сочи на неделю в ноябре вдвоём", "user_id": "u_test1"}).json()
    c.post("/api/select", json={"session_id": r["session_id"], "kind": "destination", "id": "sochi"})  # no user_id here
    c.post("/api/checkout", json={"session_id": r["session_id"]})
    assert db.list_bookings("u_test1"), "booking must be linked to the user even after select()"


def test_parallel_requests_same_session():
    sid = _plan()["session_id"]
    codes = []
    def go(m):
        codes.append(c.post("/api/chat", json={"message": m, "session_id": sid}).status_code)
    ts = [threading.Thread(target=go, args=(m,)) for m in ["дешевле", "на 2 дня дольше", "с собакой", "без пересадок"]]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert codes == [200] * 4
    assert c.get(f"/api/session/{sid}").status_code == 200


# ---- parser / dates
def test_numeric_dates():
    d = parser.sanitize(parser.parse("в Сочи с 10.12 по 15.12 вдвоём", TODAY, {}), TODAY)
    assert d["start_date"] == "2026-12-10" and d["nights"] == 5
    d = parser.sanitize(parser.parse("с 25.12 по 3.01", TODAY, {}), TODAY)
    assert d["start_date"] == "2026-12-25" and d["nights"] == 9
    d = parser.sanitize(parser.parse("с 3.01.2027 по 10.01.2027", TODAY, {}), TODAY)
    assert d["start_date"] == "2027-01-03" and d["nights"] == 7
    assert "start_date" not in parser.parse("перелёт не больше 2.5 часа", TODAY, {})


def test_dates_never_in_past():
    d = parser.sanitize(parser.parse("с 1 по 5 октября", TODAY, {}), TODAY)
    assert d["start_date"] == "2027-10-01"
    late = date(2026, 12, 30)
    d = parser.sanitize(parser.parse("на новый год", late, {}), late)
    assert d["start_date"] >= late.isoformat()


def test_sanitize_rejects_garbage_llm_output():
    bad = {"start_date": "вчера", "nights": "-5", "adults": 0, "children": 99, "budget": "много",
           "destination": "atlantis", "meals": "XX", "moods": ["sea", "lava"], "pets": True, "max_travel_hours": 999}
    s = parser.sanitize(bad, TODAY)
    assert "start_date" not in s and s["nights"] == 1 and s["adults"] == 1 and s["children"] == parser.MAX_CHILDREN
    assert "budget" not in s and "destination" not in s and "meals" not in s and s["moods"] == ["sea"]
    assert "max_travel_hours" not in s and s["pets"] is True


def test_extend_nights_is_clamped():
    st = agent.new_state()
    agent.handle_message(st, "Сочи на неделю в ноябре вдвоём", {})
    agent.handle_message(st, "на 90 дней дольше", {})
    assert st["req"]["nights"] <= parser.MAX_NIGHTS


# ---- guest / price math
def test_infant_fare_cheaper_than_child():
    base = {"adults": 2, "children": 1}
    assert tools.child_fare_units({**base, "child_ages": [1]}, 0.75) == tools.INFANT_FARE_SHARE
    assert tools.child_fare_units({**base, "child_ages": [7]}, 0.75) == 0.75
    assert tools.child_fare_units(base, 0.75) == 0.75  # unknown age → regular child fare


def test_big_group_transfer_needs_several_cars():
    req = {"adults": 9, "children": 0, "start_date": "2026-11-10"}
    for t in tools.search_transfers(req, "sochi"):
        if t["per"] == "car" and t["max_pax"] < 9:
            assert t["total"] >= 2 * t["price"]
