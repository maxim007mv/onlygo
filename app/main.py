"""FastAPI app: chat UI at /, JSON API under /api, health at /health."""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, datastore, db, llm, tools, trip

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("onlygo")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    log.info("purged %d stale sessions (ttl=%sd)", db.purge_sessions(), db.SESSION_TTL_DAYS)
    yield


app = FastAPI(title="OnlyGo — О!дин запрос", version="0.1.0", lifespan=_lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


MAX_MESSAGE = 2000
ID = dict(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:\-]+$")
SelectKind = Literal["destination", "hotel", "outbound", "return", "transfer_out", "transfer_ret"]
PostAction = Literal["early_checkin", "late_checkout", "change_dates", "reminder", "add_transfer"]
MAX_DATE_SHIFT = 30
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "120"))  # per client IP on /api/*; 0 disables
PURGE_EVERY_SAVES = 200  # run session TTL cleanup every N chat turns


class ChatIn(BaseModel):
    message: str = Field(max_length=MAX_MESSAGE * 5)  # longer input is truncated, absurd input rejected
    session_id: str | None = Field(None, **ID)
    user_id: str | None = Field(None, **ID)


class SelectIn(BaseModel):
    session_id: str = Field(**ID)
    kind: SelectKind
    id: str = Field(min_length=1, max_length=128)


class CorrectIn(BaseModel):
    session_id: str = Field(**ID)
    slot: str = Field(min_length=1, max_length=64)


class CheckoutIn(BaseModel):
    session_id: str = Field(**ID)
    name: str = Field("Гость", max_length=120)
    email: str = Field("guest@example.com", max_length=200)
    card: str = Field("4242 4242 4242 4242", max_length=32)


class PostIn(BaseModel):
    booking_id: str = Field(**ID)
    action: PostAction
    value: str | None = Field(None, max_length=16)


# One lock per session: chat/select/correct/checkout are read-modify-write on the same JSON state,
# and FastAPI runs sync endpoints in a thread pool — without this, parallel clicks lose updates.
_session_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
_locks_guard = threading.Lock()


def session_lock(sid: str) -> threading.Lock:
    with _locks_guard:
        if len(_session_locks) > 10_000:
            _session_locks.clear()
        return _session_locks[sid]


_hits: defaultdict[str, deque] = defaultdict(deque)
_hits_guard = threading.Lock()
_chat_saves = 0


def rate_limited(client: str, now: float | None = None, limit: int | None = None) -> bool:
    """Sliding 60-s window per client. In-memory: enough for one demo process (Redis for several workers)."""
    limit = RATE_LIMIT_PER_MIN if limit is None else limit
    if limit <= 0:
        return False
    now = time.monotonic() if now is None else now
    with _hits_guard:
        if len(_hits) > 10_000:  # bound memory under many distinct IPs
            _hits.clear()
        q = _hits[client]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
    return False


@app.middleware("http")
async def _rate_limit(request: Request, call_next):
    if request.url.path.startswith("/api/") and rate_limited(request.client.host if request.client else "?"):
        return JSONResponse(status_code=429, content={"detail": "Слишком много запросов — подождите минуту"})
    return await call_next(request)


def _maybe_purge() -> None:
    global _chat_saves
    _chat_saves += 1
    if _chat_saves % PURGE_EVERY_SAVES == 0:
        db.purge_sessions()


@app.exception_handler(agent.AgentInputError)
def _agent_input_error(_: Request, exc: agent.AgentInputError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(RequestValidationError)
def _validation_error(_: Request, exc: RequestValidationError):
    errs = [{"field": ".".join(map(str, e.get("loc", [])[1:])), "msg": e.get("msg")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "Некорректный запрос", "errors": errs})


@app.exception_handler(Exception)
def _unexpected(_: Request, exc: Exception):  # last resort: JSON, no stack trace to the client
    log.exception("unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Внутренняя ошибка агента — попробуйте переформулировать запрос"})


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
    return {
        "mode": "llm" if llm.enabled() else "offline",
        "model": llm.model() if llm.enabled() else None,
        "data": "synthetic/mock",
        "destinations": len(datastore.destinations()),
    }


@app.post("/api/chat")
def chat(inp: ChatIn):
    sid = inp.session_id or uuid.uuid4().hex[:12]
    text = inp.message.strip()[:MAX_MESSAGE]
    if not text:
        raise HTTPException(400, "Пустое сообщение — опишите поездку")
    with session_lock(sid):
        state = db.get_session(sid) or agent.new_state()
        profile = db.get_profile(inp.user_id) if inp.user_id else {}
        resp = agent.handle_message(state, text, profile)
        db.save_session(sid, inp.user_id, state)
        _remember(inp.user_id, state)
    _maybe_purge()
    log.info("chat sid=%s turn=%s plan=%s q=%d", sid, state["turns"], bool(resp.get("plan")), len(resp["questions"]))
    return {"session_id": sid, **resp}


@app.post("/api/select")
def select(inp: SelectIn):
    with session_lock(inp.session_id):
        state = _load(inp.session_id)
        resp = agent.select(state, inp.kind, inp.id)
        db.save_session(inp.session_id, None, state)
    return {"session_id": inp.session_id, **resp}


@app.post("/api/correct")
def correct(inp: CorrectIn):
    with session_lock(inp.session_id):
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
    with session_lock(inp.session_id):
        return _checkout(inp)


def _plan_sig(p: dict) -> str:
    return "|".join([p["start_date"], str(p["total"])] + [str(i.get("id")) for i in p["items"]])


def _checkout(inp: CheckoutIn):
    state = _load(inp.session_id)
    p = state.get("plan")
    if not p:
        raise HTTPException(400, "no plan to pay")
    errors = [c for c in p["checks"] if c["level"] == "error"]
    if errors:
        raise HTTPException(409, {"message": "В плане есть ошибки стыковок", "errors": errors})
    prev = state.get("booking")
    if prev and prev.get("plan_sig") == _plan_sig(p):  # double click / retry → same booking, no second charge
        old = db.get_booking(prev["booking_id"]) or {}
        return {**{k: v for k, v in old.items() if k not in ("plan", "payer", "actions", "session_id")}, "duplicate": True}
    res = trip.simulate_payment(p, {"name": inp.name, "email": inp.email, "card": inp.card})
    if res["status"] == "paid":
        booking = {**res, "session_id": inp.session_id, "plan": p, "payer": {"name": inp.name, "email": inp.email}, "actions": []}
        db.save_booking(res["booking_id"], inp.session_id, db.session_user(inp.session_id), booking)
        state["booking"] = {**{k: booking[k] for k in ("booking_id", "amount", "status", "paid_at")}, "plan_sig": _plan_sig(p)}
        db.save_session(inp.session_id, None, state)
    return res


@app.post("/api/post_purchase")
def post_purchase(inp: PostIn):
    b = db.get_booking(inp.booking_id)
    if not b:
        raise HTTPException(404, "booking not found")
    p = b["plan"]
    hotel = datastore.hotel(next((i.get("id") for i in p["items"] if i["kind"] == "hotel"), ""))
    if not hotel:
        raise HTTPException(409, "В бронировании нет отеля")
    msg = _post_message(inp.action, hotel, date.fromisoformat(p["start_date"]), inp.value)
    b["actions"].append({"action": inp.action, "message": msg, "at": datetime.now(tools.LOCAL_TZ).isoformat(timespec="seconds")})
    db.save_booking(inp.booking_id, b["session_id"], db.session_user(b["session_id"]), b)
    return {"booking_id": inp.booking_id, "message": msg, "actions": b["actions"]}


def _parse_shift(value: str | None) -> int:
    try:
        shift = int(value or 1)
    except ValueError:
        raise HTTPException(422, "value для change_dates — целое число дней") from None
    if not shift or abs(shift) > MAX_DATE_SHIFT:
        raise HTTPException(422, f"Сдвиг дат: от -{MAX_DATE_SHIFT} до {MAX_DATE_SHIFT} дней, не 0")
    return shift


def _post_message(action: str, hotel: dict, start: date, value: str | None) -> str:
    """Simulated after-sale actions; every message says «симуляция»."""
    name = hotel["name"]
    if action == "early_checkin":
        ok = hotel["early_checkin_available"]
        answer = "отель подтвердил заезд с 10:00, доплата 30% ночи." if ok else "ранний заезд не гарантирован, отель ответит за сутки."
        return f"Запрос на ранний заезд отправлен в {name} (симуляция): {answer}"
    if action == "late_checkout":
        return f"Запрос на поздний выезд до 18:00 отправлен в {name} (симуляция)."
    if action == "change_dates":
        shift = _parse_shift(value)
        tail = (
            "отель меняет даты бесплатно; авиабилеты — по тарифу, разница пересчитана (симуляция)."
            if hotel["free_cancellation"]
            else f"тариф отеля невозвратный — возможен штраф. Рекомендую пересобрать план в чате: «на {shift} дня позже»."
        )
        return f"Перенос на {shift:+d} дн. ({(start + timedelta(days=shift)):%d.%m}): {tail}"
    if action == "reminder":
        return (
            f"Напоминания созданы: онлайн-регистрация за 24 ч до вылета, выезд в аэропорт, документы — "
            f"{(start - timedelta(days=3)):%d.%m} (симуляция)."
        )
    if action == "add_transfer":
        return "Трансфер привязан к адресу отеля и номеру рейса; водитель будет ждать с табличкой (симуляция)."
    raise HTTPException(400, "unknown action")


@app.get("/api/profile/{uid}")
def profile(uid: str):
    if len(uid) > 64:
        raise HTTPException(422, "user_id слишком длинный")
    return db.get_profile(uid)


@app.delete("/api/profile/{uid}")
def forget(uid: str):
    db.save_profile(uid, {})
    return {"status": "forgotten"}


@app.get("/api/destinations")
def destinations():
    return {"mock": True, "items": datastore.destinations()}
