"""End-to-end demo scenario against a running server (default http://localhost:8080).
Walks: one request -> clarification -> 2 replans -> fix a connection warning -> simulated payment -> post-purchase.
Usage: python scripts/demo_e2e.py [--url http://localhost:8080]   (stdlib only)"""

import json
import sys
import urllib.request

URL = sys.argv[sys.argv.index("--url") + 1] if "--url" in sys.argv else "http://localhost:8080"


def call(path, body=None):
    req = urllib.request.Request(URL + path, json.dumps(body).encode() if body is not None else None, {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def show(step, r):
    p = r.get("plan") or {}
    print(f"\n== {step}\n{r['reply']}")
    for c in r.get("changes") or []:
        print("  Δ", c)
    for q in r.get("questions") or []:
        print("  ?", q["text"])
    if p:
        for c in p["checks"]:
            if c["level"] != "ok":
                print(f"  [{c['level']}] {c['title']}: {c['text']}" + (f"  → fix: {c['fix']['label']}" if c.get("fix") else ""))
        print(f"  итого: {p['total']:,} ₽".replace(",", " "))


def main():
    assert call("/health") == {"status": "ok"}
    print("health ok, mode:", call("/api/config")["mode"])
    r = call(
        "/api/chat", {"message": "хочу куда-нибудь к тёплому морю в ноябре, недорого, с ребёнком, без долгих перелётов", "user_id": "demo"}
    )
    sid = r["session_id"]
    show("1. Один запрос", r)
    for i, m in enumerate(["ребёнку 5 лет", "на 2 дня позже", "мы поедем с собакой"], 2):
        r = call("/api/chat", {"message": m, "session_id": sid, "user_id": "demo"})
        show(f"{i}. «{m}»", r)
    fix = next((c["fix"] for c in r["plan"]["checks"] if c.get("fix") and "id" in c["fix"]), None)
    if fix:
        r = call("/api/select", {"session_id": sid, "kind": fix["kind"], "id": fix["id"]})
        show(f"5. Исправление: {fix['label']}", r)
    assert r["plan"]["connection_errors"] == 0
    pay = call("/api/checkout", {"session_id": sid, "name": "IVAN PETROV", "email": "ivan@example.com", "card": "4242424242424242"})
    print("\n== 6. Оплата (симуляция):", pay["status"], pay["booking_id"], pay["amount"])
    pp = call("/api/post_purchase", {"booking_id": pay["booking_id"], "action": "early_checkin"})
    print("== 7. После покупки:", pp["message"])
    print("\nDEMO OK")


if __name__ == "__main__":
    main()
