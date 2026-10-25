"""FastAPI app: chat UI at /, JSON API under /api, health at /health."""
from __future__ import annotations

import logging
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, datastore, db, llm, tools

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("onlygo")
STATIC = Path(__file__).parent / "static"
app = FastAPI(title="OnlyGo — О!дин запрос", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class ChatIn(BaseModel):
    message: str
    session_id: str | None = None
    user_id: str | None = None


class SelectIn(BaseModel):
    session_id: str
    kind: str
    id: str


class CorrectIn(BaseModel):
    session_id: str
    slot: str


class CheckoutIn(BaseModel):
    session_id: str
    name: str = "Гость"
    email: str = "guest@example.com"
    card: str = "4242 4242 4242 4242"


class PostIn(BaseModel):
    booking_id: str
    action: str  # early_checkin | late_checkout | change_dates | reminder | add_transfer
    value: str | None = None


def _load(sid: str) -> dict:
    st = db.get_session(sid)
    if not st:
        raise HTTPException(404, "session not found")
    return st


def _remember(uid: str | None, state: dict):
    if not uid:
        return
    prof = db.get_profile(uid)
    r = state["req"]
    for k in ("origin", "pets", "meals", "children", "child_ages", "adults"):
        if r.get(k) not in (None, [], False):
            prof[k] = r[k]
    prof["last_destination"] = state["selected"].get("destination")
    prof["trips_planned"] = prof.get("trips_planned", 0) + (1 if state["turns"] == 1 else 0)
    db.save_profile(uid, prof)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def config():
    return {"mode": "llm" if llm.enabled() else "offline", "model": llm.model() if llm.enabled() else None,
            "data": "synthetic/mock", "destinations": len(datastore.destinations())}


@app.post("/api/chat")
def chat(inp: ChatIn):
    sid = inp.session_id or uuid.uuid4().hex[:12]
    state = db.get_session(sid) or agent.new_state()
    profile = db.get_profile(inp.user_id) if inp.user_id else {}
    resp = agent.handle_message(state, inp.message.strip()[:2000], profile)
    db.save_session(sid, inp.user_id, state)
    _remember(inp.user_id, state)
    log.info("chat sid=%s turn=%s plan=%s q=%d", sid, state["turns"], bool(resp.get("plan")), len(resp["questions"]))
    return {"session_id": sid, **resp}


@app.post("/api/select")
def select(inp: SelectIn):
    state = _load(inp.session_id)
    resp = agent.select(state, inp.kind, inp.id)
    db.save_session(inp.session_id, None, state)
    return {"session_id": inp.session_id, **resp}


@app.post("/api/correct")
def correct(inp: CorrectIn):
    state = _load(inp.session_id)
    resp = agent.correct(state, inp.slot)
    db.save_session(inp.session_id, None, state)
    return {"session_id": inp.session_id, **resp}


@app.get("/api/session/{sid}")
def session(sid: str):
    st = _load(sid)
    return agent.finalize(st, {"reply": "", "questions": [], "plan": st["plan"], "changes": [], "trace": []})


@app.post("/api/checkout")
def checkout(inp: CheckoutIn):
    state = _load(inp.session_id)
    p = state.get("plan")
    if not p:
        raise HTTPException(400, "no plan to pay")
    errors = [c for c in p["checks"] if c["level"] == "error"]
    if errors:
        raise HTTPException(409, {"message": "В плане есть ошибки стыковок", "errors": errors})
    res = tools.simulate_payment(p, {"name": inp.name, "email": inp.email, "card": inp.card})
    if res["status"] == "paid":
        booking = {**res, "session_id": inp.session_id, "plan": p, "payer": {"name": inp.name, "email": inp.email}, "actions": []}
        uid = None
        db.save_booking(res["booking_id"], inp.session_id, uid, booking)
        state["booking"] = {k: booking[k] for k in ("booking_id", "amount", "status", "paid_at")}
        db.save_session(inp.session_id, None, state)
    return res


@app.post("/api/post_purchase")
def post_purchase(inp: PostIn):
    b = db.get_booking(inp.booking_id)
    if not b:
        raise HTTPException(404, "booking not found")
    p = b["plan"]
    hotel = datastore.hotel(next(i["id"] for i in p["items"] if i["kind"] == "hotel"))
    start = date.fromisoformat(p["start_date"])
    if inp.action == "early_checkin":
        ok = hotel["early_checkin_available"]
        msg = f"Запрос на ранний заезд отправлен в {hotel['name']} (симуляция): " + ("отель подтвердил заезд с 10:00, доплата 30% ночи." if ok else "ранний заезд не гарантирован, отель ответит за сутки.")
    elif inp.action == "late_checkout":
        msg = f"Запрос на поздний выезд до 18:00 отправлен в {hotel['name']} (симуляция)."
    elif inp.action == "change_dates":
        shift = int(inp.value or 1)
        free = hotel["free_cancellation"]
        msg = f"Перенос на {shift:+d} дн. ({(start + timedelta(days=shift)):%d.%m}): " + ("отель меняет даты бесплатно; авиабилеты — по тарифу, разница пересчитана (симуляция)." if free else "тариф отеля невозвратный — возможен штраф. Рекомендую пересобрать план в чате: «на %d дня позже»." % shift)
    elif inp.action == "reminder":
        msg = f"Напоминания созданы: онлайн-регистрация за 24 ч до вылета, выезд в аэропорт, документы — {(start - timedelta(days=3)):%d.%m} (симуляция)."
    elif inp.action == "add_transfer":
        msg = "Трансфер привязан к адресу отеля и номеру рейса; водитель будет ждать с табличкой (симуляция)."
    else:
        raise HTTPException(400, "unknown action")
    b["actions"].append({"action": inp.action, "message": msg, "at": datetime.now().isoformat(timespec="seconds")})
    db.save_booking(inp.booking_id, b["session_id"], None, b)
    return {"booking_id": inp.booking_id, "message": msg, "actions": b["actions"]}


@app.get("/api/profile/{uid}")
def profile(uid: str):
    return db.get_profile(uid)


@app.delete("/api/profile/{uid}")
def forget(uid: str):
    db.save_profile(uid, {})
    return {"status": "forgotten"}


@app.get("/api/destinations")
def destinations():
    return {"mock": True, "items": datastore.destinations()}
