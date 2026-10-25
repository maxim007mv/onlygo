"""Quality metrics over a fixed test query set.

Usage:
  python tests/run_metrics.py                         # in-process (no server needed)
  python tests/run_metrics.py --url http://localhost:8080   # against a running server
  docker compose exec app python tests/run_metrics.py

Metrics: completion rate (plan assembled + simulated payment succeeded), clarifying
questions per dialog, turns to first recommendation, time-to-first-recommendation (ms),
connection errors in final plans, expectation hit rate, re-plan success (date shift).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def client_factory(url: str | None):
    if url:
        import httpx
        c = httpx.Client(base_url=url, timeout=60)
        return lambda p, b: c.post(p, json=b)
    os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "metrics.db"))
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    return lambda p, b: c.post(p, json=b)


def check_expect(plan: dict, exp: dict) -> bool:
    if not plan:
        return False
    d = plan["destination"]
    ok = True
    if "sea" in exp: ok &= d["weather"]["sea"] is not None
    if "destination" in exp: ok &= d["id"] == exp["destination"]
    if "tags" in exp: ok &= exp["tags"] in d["tags"]
    if "max_hours" in exp: ok &= d["flight_hours"] <= exp["max_hours"]
    return bool(ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    args = ap.parse_args()
    post = client_factory(args.url)
    cases = json.loads((ROOT / "tests" / "queries.json").read_text(encoding="utf-8"))
    rows = []
    for case in cases:
        sid, t0, first_ms, first_turn, clar, r = None, time.perf_counter(), None, None, 0, {}
        for i, msg in enumerate(case["turns"], 1):
            r = post("/api/chat", {"message": msg, "session_id": sid, "user_id": f"metrics-{case['id']}"}).json()
            sid = r["session_id"]
            clar += len(r["questions"])
            if r.get("plan") and first_ms is None:
                first_ms, first_turn = round((time.perf_counter() - t0) * 1000, 1), i
        plan = r.get("plan")
        replan_ok = False
        paid = False
        if plan:
            r2 = post("/api/chat", {"message": "на 2 дня позже", "session_id": sid}).json()
            replan_ok = bool(r2.get("plan")) and r2["plan"]["start_date"] != plan["start_date"] and any(t["status"] == "reused" for t in r2["trace"])
            plan = r2.get("plan") or plan
            pay = post("/api/checkout", {"session_id": sid})
            paid = pay.status_code == 200 and pay.json().get("status") == "paid"
        rows.append(dict(id=case["id"], completed=bool(plan) and paid, clarifications=clar, first_rec_turn=first_turn,
                         first_rec_ms=first_ms, connection_errors=(plan or {}).get("connection_errors", 0),
                         warnings=sum(1 for c in (plan or {}).get("checks", []) if c["level"] == "warn"),
                         expectation_hit=check_expect(plan, case["expect"]), replan_ok=replan_ok,
                         destination=(plan or {}).get("destination", {}).get("name"), total=(plan or {}).get("total")))
    n = len(rows)
    ms = [r["first_rec_ms"] for r in rows if r["first_rec_ms"] is not None]
    summary = dict(
        cases=n,
        completion_rate=round(sum(r["completed"] for r in rows) / n, 3),
        avg_clarifications=round(statistics.mean(r["clarifications"] for r in rows), 2),
        avg_turns_to_first_rec=round(statistics.mean(r["first_rec_turn"] for r in rows if r["first_rec_turn"]), 2),
        median_time_to_first_rec_ms=round(statistics.median(ms), 1) if ms else None,
        connection_errors_total=sum(r["connection_errors"] for r in rows),
        expectation_hit_rate=round(sum(r["expectation_hit"] for r in rows) / n, 3),
        replan_success_rate=round(sum(r["replan_ok"] for r in rows) / n, 3),
    )
    print(f"{'case':16} {'done':5} {'clar':4} {'1st@':4} {'ms':>7} {'err':3} {'exp':4} {'replan':6} destination / total")
    for r in rows:
        print(f"{r['id']:16} {str(r['completed']):5} {r['clarifications']:<4} {str(r['first_rec_turn']):4} {str(r['first_rec_ms']):>7} {r['connection_errors']:<3} {str(r['expectation_hit']):4} {str(r['replan_ok']):6} {r['destination']} / {r['total']}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    out = ROOT / "tests" / "metrics_report.json"
    try:
        out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2))
    except OSError:
        pass
    return 0 if summary["completion_rate"] >= 0.8 else 1


if __name__ == "__main__":
    sys.exit(main())
