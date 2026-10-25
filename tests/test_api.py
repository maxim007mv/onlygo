"""Smoke/e2e tests (offline mode). Run: pytest -q"""
import os
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ["OPENAI_API_KEY"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

c = TestClient(app)


def test_health():
    r = c.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_ui():
    assert "OnlyGo" in c.get("/").text


def test_full_flow_offline():
    r = c.post("/api/chat", json={"message": "хочу к тёплому морю, с ребёнком, без долгих перелётов", "user_id": "t"}).json()
    assert r["mode"] == "offline" and r["questions"] and r["plan"] is None  # dates are blocking
    sid = r["session_id"]
    r = c.post("/api/chat", json={"message": "в ноябре на неделю, ребенку 5 лет", "session_id": sid}).json()
    p = r["plan"]
    assert p and p["destination"]["weather"]["sea"] >= 24 and p["connection_errors"] == 0
    assert r["candidates"]["hotels"] and r["candidates"]["outbound"]
    old_start = p["start_date"]
    r = c.post("/api/chat", json={"message": "на 2 дня позже", "session_id": sid}).json()
    assert r["plan"]["start_date"] != old_start
    assert any(t["status"] == "reused" for t in r["trace"])  # incremental re-plan
    r = c.post("/api/chat", json={"message": "мы едем с собакой", "session_id": sid}).json()
    hid = next(i["id"] for i in r["plan"]["items"] if i["kind"] == "hotel")
    from app import datastore
    assert datastore.hotel(hid)["pets_allowed"]
    pay = c.post("/api/checkout", json={"session_id": sid}).json()
    assert pay["status"] == "paid"
    pp = c.post("/api/post_purchase", json={"booking_id": pay["booking_id"], "action": "early_checkin"}).json()
    assert "ранний" in pp["message"].lower()


def test_declined_card():
    r = c.post("/api/chat", json={"message": "в Сочи в декабре на неделю вдвоём"}).json()
    pay = c.post("/api/checkout", json={"session_id": r["session_id"], "card": "4000 0000 0000 0002"}).json()
    assert pay["status"] == "declined"


def test_actionable_fix_and_demo_flow():
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    r = c.post("/api/chat", json={"message": "хочу куда-нибудь к тёплому морю в ноябре, недорого, с ребёнком, без долгих перелётов"}).json()
    fixes = [ch["fix"] for ch in r["plan"]["checks"] if ch.get("fix") and "id" in ch["fix"]]
    if fixes:
        r2 = c.post("/api/select", json={"session_id": r["session_id"], "kind": fixes[0]["kind"], "id": fixes[0]["id"]}).json()
        assert r2["plan"]["connection_errors"] == 0
    assert any("Итог" in x for x in c.post("/api/chat", json={"message": "на 2 дня позже", "session_id": r["session_id"]}).json()["changes"])
