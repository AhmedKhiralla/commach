"""Phase 6: results analysis, run-hours triggers, the Complanning connection and the Ask ComMach assistant."""
import datetime as dt
import json
import urllib.request

import psycopg
import pytest

from conftest import sign_in_first_time

PW = "Anal#Admin2026x"


@pytest.fixture(scope="module")
def demo(server, platform):
    status, body, _ = platform.post("/api/platform/tenants", {"code": "ANAL", "name": "Analysis Pharma",
                                                              "admin": {"email": "admin@anal.test", "full_name": "Ana Lysis"}})
    assert status == 200, body
    admin = sign_in_first_time(server, "admin@anal.test", body["temporary_password"], PW)
    status, out, _ = admin.post("/api/sample-data", {"give_me_roles": True})
    assert status == 200 and out["meter_readings"] > 50 and out["production_windows"] > 20 and out["hours_triggers"] == 2, out
    return {"admin": admin, "site_id": out["site_id"]}


def api(server, key, method, path, body=None):
    req = urllib.request.Request(server + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_kpis_drift_and_intervals(demo):
    a = demo["admin"]
    status, s, _ = a.get("/api/analysis/summary?kind=calibration&months=12")
    assert status == 200 and s["completed"] > 50 and 0 < s["on_time_pct"] <= 100 and s["oot"] >= 1
    assert len(s["monthly"]) == 12 and s["vendors"] and s["by_class"]
    assert s["first_pass_pct"] + 100 * s["oot"] / s["completed"] == pytest.approx(100, abs=0.2)
    status, pm, _ = a.get("/api/analysis/summary?kind=pm")
    assert status == 200 and pm["completed"] > 50 and pm["man_hours"] > 0 and pm["due_by_hours"] >= 1
    _, tasks, _ = a.get("/api/tasks?kind=calibration&state=completed&q=THG-110")
    status, d, _ = a.get(f"/api/analysis/drift?asset_id={tasks['items'][0]['asset_id']}")
    assert status == 200 and {x["parameter"] for x in d["series"]} == {"Temperature", "Relative humidity"}
    assert all(len(x["points"]) >= 2 for x in d["series"])
    status, rec, _ = a.get("/api/analysis/intervals")
    assert status == 200 and rec["items"] and rec["unchanged"] > 0
    assert {"shorten"} <= {i["direction"] for i in rec["items"]}
    for i in rec["items"]:
        assert i["reason"] in ("last_oot", "drift", "stable")
        assert (i["direction"] == "shorten") == (i["reason"] != "stable")
    assert a.get("/api/analysis/summary?kind=nope")[0] == 422


def test_run_hours_trigger(demo, db_url):
    a = demo["admin"]
    _, pm, _ = a.get("/api/tasks?kind=pm&state=open")
    # the demo has one machine whose run-hours are used up: its PM was brought forward to today
    byh = [t for t in (a.get(f"/api/tasks/{x['id']}")[1] for x in pm["items"][:200]) if t.get("hours")]
    assert len(byh) == 2
    due = [t for t in byh if t["hours"]["due"]]
    assert len(due) == 1 and due[0]["due_by_hours"] and due[0]["hours"]["remaining"] < 0
    near = [t for t in byh if not t["hours"]["due"]][0]
    assert not near["due_by_hours"] and near["hours"]["projected_date"]
    asset = near["asset_id"]
    status, m, _ = a.get(f"/api/assets/{asset}/meter")
    assert status == 200 and m["meter_unit"] == "hours" and m["can_enter"] and m["schedules"][0]["hours"]["remaining"] > 0
    # a lower reading is refused; a reset needs a note
    assert a.post(f"/api/assets/{asset}/meter", {"reading": 1})[0] == 422
    assert a.post(f"/api/assets/{asset}/meter", {"reading": 1, "meter_reset": True})[0] == 422
    status, m2, _ = a.post(f"/api/assets/{asset}/meter", {"reading": float(m["last"]) + 200})
    assert status == 200 and m2["tasks_brought_forward"] == 1
    _, t, _ = a.get(f"/api/tasks/{near['id']}")
    assert t["due_by_hours"] and t["hours"]["due"] and dt.date.fromisoformat(t["due_date"]) <= dt.date.today() + dt.timedelta(days=4)
    with psycopg.connect(db_url, autocommit=True) as conn:
        with pytest.raises(psycopg.Error):
            conn.execute("UPDATE meter_readings SET reading = 0 WHERE asset_id = %s", (asset,))


def test_complanning_connection(demo, server):
    a = demo["admin"]
    _, k, _ = a.post("/api/integration/keys", {"name": "Complanning"})
    key = k["key"]
    status, ev = api(server, key, "GET", "/api/integration/v1/events?after=0&limit=1000")
    assert status == 200 and {"calibration.planned", "maintenance.planned"} <= {e["event"] for e in ev["items"]}
    planned = next(e for e in ev["items"] if e["event"] == "maintenance.planned")
    assert {"number", "asset_code", "window_start", "window_end", "due_date"} <= set(planned["payload"])
    # the released production plan: one long campaign on a machine with an open PM, leaving one gap
    _, pm, _ = a.get("/api/tasks?kind=pm&state=open&q=TP-")
    t = next(x for x in pm["items"] if dt.date.fromisoformat(x["due_date"]) >= dt.date.today() + dt.timedelta(days=5))
    status, _ = api(server, key, "POST", "/api/integration/v1/production-plan", {"bad": 1})
    assert status == 422
    day = dt.date.fromisoformat(t["due_date"])
    start = dt.datetime.combine(day - dt.timedelta(days=20), dt.time(), dt.timezone.utc)
    gap = dt.datetime.combine(day + dt.timedelta(days=2), dt.time(6), dt.timezone.utc)
    plan = {"start": start.isoformat(), "end": (start + dt.timedelta(days=60)).isoformat(),
            "machines": [{"code": t["asset_code"], "campaigns": [
                {"start": start.isoformat(), "end": gap.isoformat(), "label": "Paracetamol", "ref": "C1"},
                {"start": (gap + dt.timedelta(hours=14)).isoformat(), "end": (start + dt.timedelta(days=60)).isoformat(), "label": "Metformin"}]},
                {"code": "NOPE-1", "campaigns": []}]}
    status, out = api(server, key, "POST", "/api/integration/v1/production-plan", plan)
    assert status == 200 and out["campaigns"] == 2 and out["unknown"] == ["NOPE-1"]
    _, td, _ = a.get(f"/api/tasks/{t['id']}")
    p = td["production"]
    assert p and p["conflict"] is True and p["suggestion"]["start"].startswith(gap.date().isoformat())
    _, g, _ = a.get("/api/analysis/pm-gaps")
    assert any(x["id"] == t["id"] and x["conflict"] for x in g["items"])
    # run-hours from Complanning
    _, m, _ = a.get(f"/api/assets/{t['asset_id']}/meter")
    now = dt.datetime.now(dt.timezone.utc)
    status, r = api(server, key, "POST", "/api/integration/v1/meter-readings", {"readings": [
        {"code": t["asset_code"], "reading": float(m["last"] or 0) + 50, "read_at": now.isoformat(), "unit": "hours"},
        {"code": t["asset_code"], "reading": 1, "read_at": now.isoformat()},
        {"code": "NOPE-1", "reading": 5, "read_at": now.isoformat()}]})
    assert status == 200 and r["stored"] == 1 and {x["reason"] for x in r["skipped"]} == {"unknown", "not_newer"}
    # moving work changes the planned window Complanning sees
    _, ev2 = api(server, key, "GET", f"/api/integration/v1/events?after={ev['last_id']}&limit=1000")
    assert ev2["last_id"] >= ev["last_id"]


def test_assistant(demo, db_url, fake_ai):
    a = demo["admin"]
    status, out, _ = a.post("/api/assistant/ask", {"question": "When was the last calibration?"})
    assert status == 200, out
    assert out["answer"].startswith("The last calibration was **CAL-") and len(out["lookups"]) == 3
    assert {l["type"] for l in out["links"]} == {"task", "asset"}
    assert out["links"][0]["href"].startswith("/calibration/tasks/")
    # the look-ups ran with the user's access and their results went back to the model
    last = fake_ai.requests[-1]["body"]["messages"][-1]["content"]
    assert last.startswith("LOOKUP RESULTS:") and "Unknown look-up" in last and '"first_pass_pct"' in last
    status, h, _ = a.get("/api/assistant/history")
    assert status == 200 and h["items"][0]["question"] == "When was the last calibration?" and h["items"][0]["lookups"]
    with psycopg.connect(db_url, autocommit=True) as conn:
        with pytest.raises(psycopg.Error):
            conn.execute("DELETE FROM assistant_questions")
    assert a.post("/api/assistant/ask", {"question": "x"})[0] == 422
