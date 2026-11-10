"""Phase 4: escalation with deviations, To-Do, e-mail outbox and digests, calendar feed, rescheduling with approval,
delegation and handover."""
import datetime as dt
import urllib.error
import urllib.request

import psycopg
import pytest

from conftest import Client, sign_in_first_time

TODAY = dt.date.today()
PW = "Sinai#Pass2026x"


def sig(c, **extra):
    return {"email": c.email, "password": PW, **extra}


@pytest.fixture(scope="module")
def org(server, platform):
    status, body, _ = platform.post("/api/platform/tenants", {"code": "SINAI", "name": "Sinai Pharma",
                                                              "admin": {"email": "admin@sinai.test", "full_name": "Sinai Admin"}})
    assert status == 200, body
    admin = sign_in_first_time(server, "admin@sinai.test", body["temporary_password"], "Sinai#Admin2026x")
    _, site, _ = admin.post("/api/sites", {"code": "ARI", "name": "Arish plant"})
    people = {}
    for key, name, roles in (("tech", "Tech A", ["maintenance_technician"]), ("tech2", "Tech B", ["maintenance_technician"]),
                             ("sup", "Sup A", ["engineering_supervisor"]), ("sup2", "Sup B", ["engineering_supervisor"]),
                             ("qa", "QA A", ["qa_approver"]), ("mgr", "Mgr A", ["engineering_manager", "qa_approver"]),
                             ("dh", "Dept Head", []), ("sh", "Site Head", [])):
        status, u, _ = admin.post("/api/users", {"email": f"{key}@sinai.test", "full_name": name,
                                                 "roles": [{"site_id": site["id"], "role": r} for r in roles]})
        assert status == 200, u
        c = sign_in_first_time(server, f"{key}@sinai.test", u["temporary_password"], PW)
        c.email, c.id = f"{key}@sinai.test", u["user"]["id"]
        people[key] = c
    assert admin.put("/api/settings/features", {"features": {"plan_approval": False}})[0] == 200
    _, dept, _ = admin.post("/api/departments", {"code": "PROD", "name_en": "Production"})
    assert admin.patch(f"/api/departments/{dept['id']}", {"head_user_id": people["dh"].id, "reason": "Head of production"})[0] == 200
    status, s2, _ = admin.patch(f"/api/sites/{site['id']}", {"head_user_id": people["sh"].id})
    assert status == 200 and s2["head_name"] == "Site Head"
    _, classes, _ = admin.get("/api/asset-classes")
    cls = {c["code"]: c["id"] for c in classes["items"]}
    sup, qa = people["sup"], people["qa"]
    assets = {}
    for code, crit in (("MX-A", "critical"), ("MX-B", "major"), ("MX-C", "major")):
        _, a, _ = sup.post("/api/assets", {"site_id": site["id"], "code": code, "name_en": f"Mixer {code}", "class_id": cls["MACHINE"],
                                           "gmp_criticality": crit, "department_id": dept["id"]})
        assets[code] = a
    assert qa.post("/api/assets/status", {"asset_ids": [a["id"] for a in assets.values()], "status": "in_service",
                                          "reason": "Released", **sig(qa)})[0] == 200
    _, t, _ = sup.post("/api/templates", {"kind": "pm", "code": "PM-MX", "title": "Mixer monthly",
                                          "items": [{"item_type": "check", "parameter": "Check seals"}]})
    assert people["mgr"].post(f"/api/templates/{t['id']}/approve", sig(people["mgr"]))[0] == 200
    tasks = {}
    for code, first in (("MX-A", TODAY - dt.timedelta(days=20)), ("MX-B", TODAY + dt.timedelta(days=3)), ("MX-C", TODAY)):
        status, out, _ = sup.post("/api/schedules", {"asset_ids": [assets[code]["id"]], "kind": "pm", "template_id": t["id"],
                                                     "interval_value": 1, "interval_unit": "month", "first_due": str(first),
                                                     "assignee_user_ids": [people["tech"].id]})
        assert status == 200 and out["created"], out
        tasks[code] = out["created"][0]["task"]
    return {"admin": admin, "site": site, "p": people, "assets": assets, "tasks": tasks, "server": server}


def test_escalation_deviation_and_digest(org, db_url):
    admin, p = org["admin"], org["p"]
    # the calendar does not move these dates: no weekend for this company
    _, ws, _ = admin.get("/api/settings/work")
    assert admin.put("/api/settings/work", {"calibration": ws["settings"]["tolerance"]["calibration"],
                                            "calendar": {"weekend": [], "closures": [], "kinds": ["pm"]},
                                            "escalation": {"accept_before_due_days": 7, "digest_hour": 0,
                                                           "deviation_after_days": {"critical": 0}}})[0] == 200
    status, out, _ = admin.post("/api/notifications/run")
    assert status == 200, out
    assert out["level1"] >= 2 and out["level2"] == 1 and out["level3"] == 1 and out["deviations"] == 1
    assert out["not_configured"] >= 1   # no Gmail password in tests: kept in the outbox, shown in the app
    _, again, _ = admin.post("/api/notifications/run")
    assert again["level1"] == again["level2"] == again["level3"] == 0 and again["digests"] == 0   # once per task and per day
    # who heard what
    _, n_tech, _ = p["tech"].get("/api/notifications")
    titles = [n["title"] for n in n_tech["items"]]
    assert any(x.startswith("Overdue: ") and "MX-A" in x for x in titles)
    assert any(x.startswith("Not accepted: ") and "MX-B" in x for x in titles)
    _, n_dh, _ = p["dh"].get("/api/notifications")
    assert {n["level"] for n in n_dh["items"]} >= {2, 3}
    _, n_sh, _ = p["sh"].get("/api/notifications")
    assert [n["level"] for n in n_sh["items"]] == [3] and "Deviation DEV-" in n_sh["items"][0]["title"]
    assert p["tech2"].get("/api/notifications")[1]["unread"] == 0
    # deviation record
    _, devs, _ = p["qa"].get("/api/deviations")
    dev = devs["items"][0]
    assert dev["asset_code"] == "MX-A" and dev["status"] == "open" and "overdue" in dev["title"]
    status, err, _ = p["qa"].post(f"/api/deviations/{dev['id']}/close", sig(p["qa"]))
    assert status == 422
    assert p["tech"].patch(f"/api/deviations/{dev['id']}", {"root_cause": "x"})[0] == 403
    status, d, _ = p["qa"].patch(f"/api/deviations/{dev['id']}", {"root_cause": "Technician on leave, no cover", "actions": "PM done; cover rota"})
    assert status == 200
    assert p["sup"].post(f"/api/deviations/{dev['id']}/close", sig(p["sup"]))[0] == 403
    status, d, _ = p["qa"].post(f"/api/deviations/{dev['id']}/close", sig(p["qa"]))
    assert status == 200 and d["status"] == "closed" and d["signature"]["user_name"] == "QA A"
    # e-mails: immediate for escalations, one digest per person
    _, log, _ = admin.get("/api/notifications/emails")
    kinds = {(e["to_email"], e["kind"]) for e in log["items"]}
    assert ("dh@sinai.test", "escalation") in kinds and ("tech@sinai.test", "digest") in kinds
    assert all(e["status"] == "not_configured" for e in log["items"])
    # mark read
    assert p["tech"].post("/api/notifications/read", {})[0] == 200
    assert p["tech"].get("/api/notifications")[1]["unread"] == 0


def test_todo_and_notify_settings(org):
    tech = org["p"]["tech"]
    status, td, _ = tech.get("/api/todo")
    assert status == 200
    assert [r["asset_code"] for r in td["overdue"]] == ["MX-A"]
    assert {r["asset_code"] for r in td["soon"]} == {"MX-B", "MX-C"}
    assert len(td["to_accept"]) == 3
    _, qa_td, _ = org["p"]["qa"].get("/api/todo")
    assert qa_td["counts"]["overdue"] == 0
    status, n, _ = tech.put("/api/me/notify", {"digest": False})
    assert status == 200 and n["digest"] is False and n["escalations"] is True and n["email_configured"] is False


def test_calendar_feed(org, server):
    tech = org["p"]["tech"]
    status, link, _ = tech.post("/api/me/calendar")
    assert status == 200 and link["url"].endswith(".ics") and link["webcal"].startswith("webcal://")
    path = link["url"].split("/api/", 1)[1]
    with urllib.request.urlopen(f"{server}/api/{path}") as r:   # no session: the token is the key
        body = r.read().decode()
        assert r.headers["Content-Type"].startswith("text/calendar")
    assert body.startswith("BEGIN:VCALENDAR") and body.count("BEGIN:VEVENT") == 3 and "MX-A" in body
    assert "\r\n" in body and "DTSTART;VALUE=DATE:" in body
    assert tech.post("/api/me/calendar/revoke")[0] == 200
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(f"{server}/api/{path}")
    assert e.value.code == 404


def test_reschedule_with_approval(org):
    p, tasks = org["p"], org["tasks"]
    tech, sup, sup2, qa, mgr = p["tech"], p["sup"], p["sup2"], p["qa"], p["mgr"]
    c = tasks["MX-C"]
    _, t, _ = tech.get(f"/api/tasks/{c['id']}")
    assert t["perms"]["reschedule"] is True
    inside = dt.date.fromisoformat(t["window_end"])
    assert tech.post(f"/api/tasks/{c['id']}/reschedule", {"new_due": str(TODAY - dt.timedelta(days=1)), "reason": "late"})[0] == 422
    status, r, _ = tech.post(f"/api/tasks/{c['id']}/reschedule", {"new_due": str(inside), "reason": "Spare part arrives Thursday"})
    assert status == 200 and r["reschedule"]["within_tolerance"] is True and r["reschedule"]["status"] == "pending"
    assert tech.post(f"/api/tasks/{c['id']}/reschedule", {"new_due": str(inside), "reason": "again"})[0] == 409
    rid = r["reschedule"]["id"]
    _, td, _ = sup.get("/api/todo")
    assert any(a["type"] == "reschedule" and a["id"] == rid for a in td["approvals"])
    assert tech.post(f"/api/reschedules/{rid}/approve", sig(tech))[0] == 403          # own request
    assert qa.post(f"/api/reschedules/{rid}/approve", sig(qa))[0] == 403              # supervisor first
    status, out, _ = sup.post(f"/api/reschedules/{rid}/approve", sig(sup))
    assert status == 200 and out["reschedules"][0]["status"] == "approved"
    _, t, _ = tech.get(f"/api/tasks/{c['id']}")
    assert t["due_date"] == str(inside) and t["window_end"] >= str(inside)
    # beyond tolerance: supervisor, then QA (another person)
    a = tasks["MX-A"]
    far = TODAY + dt.timedelta(days=10)
    status, r, _ = tech.post(f"/api/tasks/{a['id']}/reschedule", {"new_due": str(far), "reason": "Machine in campaign until then"})
    assert r["reschedule"]["within_tolerance"] is False
    rid = r["reschedule"]["id"]
    status, out, _ = mgr.post(f"/api/reschedules/{rid}/approve", sig(mgr))
    assert status == 200 and out["reschedules"][0]["status"] == "supervisor_approved"
    status, err, _ = mgr.post(f"/api/reschedules/{rid}/approve", sig(mgr))
    assert status == 403 and err["error"]["code"] == "four_eyes"
    _, td, _ = qa.get("/api/todo")
    assert any(x["type"] == "reschedule" and x["id"] == rid for x in td["approvals"])
    status, out, _ = qa.post(f"/api/reschedules/{rid}/approve", sig(qa))
    assert status == 200 and out["reschedules"][0]["status"] == "approved" and out["reschedules"][0]["qa_name"] == "QA A"
    _, t, _ = tech.get(f"/api/tasks/{a['id']}")
    assert t["due_date"] == str(far) and t["overdue"] is False
    # a rejected request leaves the date alone; the requester can withdraw
    b = tasks["MX-B"]
    _, r, _ = tech.post(f"/api/tasks/{b['id']}/reschedule", {"new_due": str(TODAY + dt.timedelta(days=40)), "reason": "busy"})
    assert sup2.post(f"/api/reschedules/{r['reschedule']['id']}/reject", {"reason": "Not a reason to move GMP work"})[0] == 200
    _, n, _ = tech.get("/api/notifications")
    assert n["items"][0]["title"].startswith("Reschedule rejected")
    _, t, _ = tech.get(f"/api/tasks/{b['id']}")
    assert t["due_date"] == b["due_date"] and t["reschedules"][0]["status"] == "rejected"


def test_delegation_and_handover(org, db_url):
    p, tasks, admin = org["p"], org["tasks"], org["admin"]
    tech, tech2 = p["tech"], p["tech2"]
    b = tasks["MX-B"]
    _, t, _ = tech2.get(f"/api/tasks/{b['id']}")
    assert t["perms"]["accept"] is False   # assigned by name to Tech A only
    status, d, _ = tech.post("/api/delegations", {"to_user_id": tech2.id, "starts_on": str(TODAY), "ends_on": str(TODAY + dt.timedelta(days=14)),
                                                  "reason": "Annual leave"})
    assert status == 200 and d["to_name"] == "Tech B"
    assert tech.post("/api/delegations", {"from_user_id": p["sup"].id, "to_user_id": tech2.id, "starts_on": str(TODAY),
                                          "ends_on": str(TODAY), "reason": "x x x"})[0] == 403
    _, td, _ = tech2.get("/api/todo")
    assert {r["asset_code"] for r in td["overdue"] + td["soon"] + td["later"]} == {"MX-A", "MX-B", "MX-C"} and td["delegated_from_names"] == ["Tech A"]
    _, t, _ = tech2.get(f"/api/tasks/{b['id']}")
    assert t["perms"]["accept"] is True
    _, mine, _ = tech2.get("/api/tasks?kind=pm&state=mine")
    assert mine["total"] == 3
    assert tech.post(f"/api/delegations/{d['id']}/end")[0] == 200
    _, t, _ = tech2.get(f"/api/tasks/{b['id']}")
    assert t["perms"]["accept"] is False
    # Tech A leaves: hand everything over to Tech B
    assert tech.post(f"/api/tasks/{b['id']}/accept")[0] == 200
    status, h, _ = admin.get(f"/api/users/{tech.id}/handover")
    assert status == 200 and h["items"]["tasks_assigned"] == 3 and h["items"]["schedules_assigned"] == 3
    assert tech.get(f"/api/users/{tech.id}/handover")[0] == 403
    status, out, _ = admin.post(f"/api/users/{tech.id}/handover", {"to_user_id": tech2.id, "reason": "Tech A left the company"})
    assert status == 200 and out["moved"]["tasks_assigned"] == 3
    assert admin.patch(f"/api/users/{tech.id}", {"status": "disabled"})[0] == 200
    _, t, _ = tech2.get(f"/api/tasks/{b['id']}")
    assert t["assignee_user_ids"] == [tech2.id] and t["perms"]["accept"] is True and t["accepted_at"] is None   # accepts again
    _, h2, _ = admin.get(f"/api/users/{tech.id}/handover")
    assert h2["items"]["total"] == 0
    _, n, _ = tech2.get("/api/notifications")
    assert any(x["kind"] == "handover" for x in n["items"])
    with psycopg.connect(db_url, autocommit=True) as conn:
        row = conn.execute("SELECT details FROM audit_log WHERE action = 'HANDOVER' ORDER BY id DESC LIMIT 1").fetchone()
        assert row is not None
