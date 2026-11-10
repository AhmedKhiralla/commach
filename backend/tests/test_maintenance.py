"""Preventive maintenance: checklists, working calendar, annual plan review and approval, execution, manual AI."""
import datetime as dt
import io

import psycopg
import pytest

from conftest import sign_in_first_time

PW = {"tech": "Techn1c#Pass2026", "sup": "Superv1#Pass2026", "mgr": "Manag3r#Pass2026", "qa": "QualityA#Pass2026"}
TODAY = dt.date.today()


def user(server, admin, email, name, roles, site_id, pw):
    status, body, _ = admin.post("/api/users", {"email": email, "full_name": name,
                                                "roles": [{"site_id": site_id, "role": r} for r in roles]})
    assert status == 200, body
    return sign_in_first_time(server, email, body["temporary_password"], pw)


def sig(email, pw, **extra):
    return {"email": email, "password": pw, **extra}


@pytest.fixture(scope="module")
def plant(server, platform):
    status, body, _ = platform.post("/api/platform/tenants", {"code": "DELTA", "name": "Delta Pharma",
                                                              "admin": {"email": "admin@delta.test", "full_name": "Delta Admin"}})
    assert status == 200, body
    admin = sign_in_first_time(server, "admin@delta.test", body["temporary_password"], "Delta#Admin2026x")
    status, site, _ = admin.post("/api/sites", {"code": "OBR", "name": "Obour plant"})
    assert status == 200, site
    tech = user(server, admin, "tech.pm@delta.test", "Tech One", ["maintenance_technician"], site["id"], PW["tech"])
    sup = user(server, admin, "sup.pm@delta.test", "Sup One", ["engineering_supervisor"], site["id"], PW["sup"])
    mgr = user(server, admin, "mgr.pm@delta.test", "Mgr One", ["engineering_manager", "qa_approver"], site["id"], PW["mgr"])
    qa = user(server, admin, "qa.pm@delta.test", "QA One", ["qa_approver"], site["id"], PW["qa"])
    _, classes, _ = admin.get("/api/asset-classes")
    cls = {c["code"]: c["id"] for c in classes["items"]}
    assets = {}
    for code in ("TPX-1", "TPX-2"):
        status, a, _ = sup.post("/api/assets", {"site_id": site["id"], "code": code, "name_en": f"Tablet press {code}",
                                                "class_id": cls["MACHINE"], "manufacturer": "Fette", "model": "P1200"})
        assert status == 200, a
        assets[code] = a
    status, out, _ = qa.post("/api/assets/status", {"asset_ids": [a["id"] for a in assets.values()], "status": "in_service",
                                                   "reason": "Released", **sig("qa.pm@delta.test", PW["qa"])})
    assert status == 200, out
    return {"admin": admin, "site": site, "tech": tech, "sup": sup, "mgr": mgr, "qa": qa, "assets": assets, "cls": cls}


ITEMS = [
    {"item_type": "check", "parameter": "Lubricate the cam tracks", "page_ref": "6-4"},
    {"item_type": "reading", "parameter": "Hydraulic pressure", "unit": "bar", "low_limit": 80, "high_limit": 120},
    {"item_type": "text", "parameter": "Running hours", "unit": "h"},
    {"item_type": "photo", "parameter": "Turret photo", "required": False},
]


def test_pm_template_and_calendar(plant):
    sup, mgr, admin = plant["sup"], plant["mgr"], plant["admin"]
    status, t, _ = sup.post("/api/templates", {"kind": "pm", "code": "pm-tp", "title": "Tablet press monthly", "items": ITEMS,
                                               "spare_parts": [{"name": "Seal kit", "part_no": "SK-1", "qty": 1}],
                                               "suggested_interval_value": 1, "suggested_interval_unit": "month", "est_minutes": 90})
    assert status == 200, t
    assert [i["item_type"] for i in t["items"]] == ["check", "reading", "text", "photo"]
    assert t["items"][1]["tol_mode"] == "limits" and float(t["items"][1]["limit_high"]) == 120 and t["items"][0]["page_ref"] == "6-4"
    assert t["spare_parts"][0]["part_no"] == "SK-1" and t["est_minutes"] == 90
    status, err, _ = sup.post("/api/templates", {"kind": "pm", "code": "pm-x", "title": "x",
                                                 "items": [{"parameter": "Mass", "nominal": 1, "tol_mode": "abs", "tol_plus": 1}]})
    assert status == 422  # calibration points do not belong in a checklist
    assert sup.post(f"/api/templates/{t['id']}/approve", sig("sup.pm@delta.test", PW["sup"]))[0] == 403
    status, t1, _ = mgr.post(f"/api/templates/{t['id']}/approve", sig("mgr.pm@delta.test", PW["mgr"]))
    assert status == 200 and t1["status"] == "approved"
    plant["template"] = t1
    # working calendar: Friday and Saturday off, a closure; PM due dates move earlier
    status, s, _ = admin.put("/api/settings/work", {"calibration": (admin.get("/api/settings/work")[1])["settings"]["tolerance"]["calibration"],
                                                    "calendar": {"weekend": [4, 5], "shift": "earlier", "kinds": ["pm"],
                                                                 "closures": [{"from": "2027-03-29", "to": "2027-04-02", "name": "Eid"}]}})
    assert status == 200, s
    assert s["settings"]["calendar"]["closures"][0]["name"] == "Eid"
    # 2027-01-01 is a Friday -> Thursday 2026-12-31; 2027-04-01 is inside the closure -> Sunday 2027-03-28
    status, p, _ = admin.post("/api/schedules/preview", {"kind": "pm", "interval_value": 3, "interval_unit": "month",
                                                         "basis": "fixed", "first_due": "2027-01-01"})
    assert p["dates"][0]["due"] == "2026-12-31" and p["dates"][0]["moved_from"] == "2027-01-01"
    assert p["dates"][1]["due"] == "2027-03-28"
    status, p, _ = admin.post("/api/schedules/preview", {"kind": "calibration", "interval_value": 3, "interval_unit": "month",
                                                         "first_due": "2027-01-01"})
    assert p["dates"][0]["due"] == "2027-01-01"   # the calendar is set for PM only


def test_plan_approval_releases_work(plant, db_url):
    sup, qa, mgr, tech = plant["sup"], plant["qa"], plant["mgr"], plant["tech"]
    site = plant["site"]["id"]
    tp1, tp2 = plant["assets"]["TPX-1"], plant["assets"]["TPX-2"]
    first = TODAY + dt.timedelta(days=3)
    while first.weekday() in (4, 5):
        first += dt.timedelta(days=1)
    status, out, _ = sup.post("/api/schedules", {"asset_ids": [tp1["id"], tp2["id"]], "kind": "pm", "template_id": plant["template"]["id"],
                                                 "interval_value": 1, "interval_unit": "month", "assignee_roles": ["maintenance_technician"],
                                                 "first_due": str(first)})
    assert status == 200 and len(out["created"]) == 2, out
    assert out["created"][0]["task"]["number"].startswith("PM-")
    # a second checklist on the same machine is allowed; the same one twice is not
    status, again, _ = sup.post("/api/schedules", {"asset_ids": [tp1["id"]], "kind": "pm", "template_id": plant["template"]["id"],
                                                   "interval_value": 1, "interval_unit": "month"})
    assert again["skipped"][0]["reason"] == "exists"
    tid = out["created"][0]["task"]["id"]
    _, t, _ = tech.get(f"/api/tasks/{tid}")
    assert t["released"] is False and t["perms"]["accept"] is False
    assert tech.post(f"/api/tasks/{tid}/accept")[0] == 409
    # the plan: supervisor reviews, QA approves
    year = int(t["due_date"][:4])
    status, plan, _ = sup.get(f"/api/plans?site_id={site}&kind=pm&year={year}")
    assert status == 200 and plan["status"] == "draft" and plan["can_review"] and not plan["can_approve"]
    assert {r["asset_code"] for r in plan["rows"]} == {"TPX-1", "TPX-2"} and plan["count"] >= 2
    assert tech.post("/api/plans/review", {"site_id": site, "kind": "pm", "year": year, **sig("tech.pm@delta.test", PW["tech"])})[0] == 403
    assert qa.post("/api/plans/approve", {"site_id": site, "kind": "pm", "year": year, **sig("qa.pm@delta.test", PW["qa"])})[0] == 403
    status, plan, _ = sup.post("/api/plans/review", {"site_id": site, "kind": "pm", "year": year, **sig("sup.pm@delta.test", PW["sup"])})
    assert status == 200 and plan["status"] == "reviewed" and plan["plan"]["reviewed_by_name"] == "Sup One"
    status, plan, _ = qa.post("/api/plans/approve", {"site_id": site, "kind": "pm", "year": year, **sig("qa.pm@delta.test", PW["qa"])})
    assert status == 200 and plan["status"] == "approved"
    assert plan["plan"]["approved_signature"]["user_name"] == "QA One"
    with psycopg.connect(db_url, autocommit=True) as conn:
        with pytest.raises(psycopg.Error):
            conn.execute("UPDATE plans SET notes = 'x' WHERE id = %s", (plan["plan"]["id"],))
    # released: the technician (assigned by role) accepts and works the checklist
    _, t, _ = tech.get(f"/api/tasks/{tid}")
    assert t["released"] is True and t["perms"]["accept"] is True
    status, t, _ = tech.post(f"/api/tasks/{tid}/accept")
    assert status == 200 and t["perms"]["edit"] is True
    res = [{"item_id": r["item_id"], "parameter": r["parameter"], "item_type": r["item_type"]} for r in t["results"]]
    res[0]["value_text"] = "ok"
    res[1]["as_found"] = 95
    body = {"performed_date": str(TODAY), "results": res, "man_hours": 1.5,
            "parts": [{"name": "Seal kit", "part_no": "SK-1", "qty": 1}]}
    status, t, _ = tech.put(f"/api/tasks/{tid}/results", body)
    assert status == 200, t
    assert t["verdict_preview"] is None   # running hours still empty
    status, err, _ = tech.post(f"/api/tasks/{tid}/complete", sig("tech.pm@delta.test", PW["tech"]))
    assert status == 422 and "checklist_open" in err["error"]["problems"]
    res[2]["value_text"] = "1530"
    res[0]["value_text"] = "maybe"
    assert tech.put(f"/api/tasks/{tid}/results", {**body, "results": res})[0] == 422
    res[0]["value_text"] = "not_ok"
    res[0]["notes"] = "Cam track scored"
    status, t, _ = tech.put(f"/api/tasks/{tid}/results", {**body, "results": res})
    assert t["verdict_preview"] == "fail" and t["parts"][0]["part_no"] == "SK-1"
    status, t, _ = tech.post(f"/api/tasks/{tid}/complete", sig("tech.pm@delta.test", PW["tech"]))
    assert status == 200, t
    assert t["verdict"] == "fail" and t["signature"]["meaning"] == "pm_completed" and t["impact"] is None
    assert t["asset"]["status"] == "in_service"   # findings do not quarantine a machine
    # fixed schedule: next due one month after the planned date, not after the date done
    assert t["next_task"]["due_date"][:7] == (dt.date.fromisoformat(out["created"][0]["task"]["due_date"]).replace(day=1)
                                              + dt.timedelta(days=32)).strftime("%Y-%m")
    # checked by the supervisor
    assert tech.post(f"/api/tasks/{tid}/review", sig("tech.pm@delta.test", PW["tech"]))[0] == 403
    status, t, _ = sup.post(f"/api/tasks/{tid}/review", sig("sup.pm@delta.test", PW["sup"]))
    assert status == 200 and t["review_signature"]["meaning"] == "pm_checked"
    # four eyes on plans: the reviewer cannot approve
    nxt = year + 3
    _, mplan, _ = mgr.get(f"/api/plans?site_id={site}&kind=pm&year={nxt}")
    assert mplan["count"] > 0
    assert mgr.post("/api/plans/review", {"site_id": site, "kind": "pm", "year": nxt, **sig("mgr.pm@delta.test", PW["mgr"])})[0] == 200
    status, err, _ = mgr.post("/api/plans/approve", {"site_id": site, "kind": "pm", "year": nxt, **sig("mgr.pm@delta.test", PW["mgr"])})
    assert status == 403 and err["error"]["code"] == "four_eyes"
    # a change after the review needs a new review
    assert sup.post("/api/schedules", {"asset_ids": [tp1["id"]], "kind": "pm", "interval_value": 1, "interval_unit": "year",
                                       "first_due": f"{nxt}-06-15"})[0] == 200
    status, err, _ = qa.post("/api/plans/approve", {"site_id": site, "kind": "pm", "year": nxt, **sig("qa.pm@delta.test", PW["qa"])})
    assert status == 409 and err["error"]["code"] == "plan_changed"


def test_plan_pdf(plant, server):
    import urllib.request
    site = plant["site"]["id"]
    c = plant["sup"]
    req = urllib.request.Request(f"{server}/api/plans/pdf?site_id={site}&kind=pm&year={TODAY.year}",
                                 headers={"X-Requested-With": "commach"})
    with c.opener.open(req) as r:
        data = r.read()
        assert r.headers["Content-Type"] == "application/pdf" and data[:4] == b"%PDF"


def _manual_pdf() -> bytes:
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for page in range(3):
        y = 800
        for line in ["FETTE P1200 OPERATING AND MAINTENANCE MANUAL", f"Chapter 6 page {page + 1}",
                     "Monthly: lubricate the cam tracks with food-grade grease.",
                     "Check the hydraulic overload pressure: 80 to 120 bar.", "Yearly: replace the turret bearings."] * 3:
            c.drawString(40, y, line)
            y -= 16
        c.showPage()
    c.save()
    return buf.getvalue()


def test_manual_ai_drafts_checklists(plant, fake_ai):
    sup = plant["sup"]
    status, f = sup.upload("manual.pdf", _manual_pdf(), "manual")
    assert status == 200, f
    status, out, _ = sup.post("/api/ai/manual", {"file_id": f["id"], "asset_id": plant["assets"]["TPX-1"]["id"]})
    assert status == 200, out
    assert out["mode"] == "text" and out["pages_read"] == 3
    prompt = str(fake_ai.requests[-1]["body"]["messages"])
    assert "--- page 2 ---" in prompt and "TPX-1" in prompt
    assert [g["title"] for g in out["groups"]] == ["Monthly maintenance", "Yearly overhaul"]   # empty group dropped
    g = out["groups"][0]
    assert [i["item_type"] for i in g["items"]] == ["check", "reading", "check"]   # unknown type -> check, blank dropped
    assert g["items"][1]["low_limit"] == 80 and g["items"][1]["high_limit"] == 120   # swapped limits fixed
    assert g["items"][2]["page_ref"] == "7" and g["spare_parts"][0]["qty"] == 1
    assert out["groups"][1]["interval_value"] == 12
    # the user turns a group into a draft checklist, changing one item
    items = [dict(i) for i in g["items"]]
    items[0]["parameter"] = "Lubricate cam tracks (Klüber grease)"
    status, t, _ = sup.post("/api/templates", {"kind": "pm", "code": "PM-TPX-M", "title": g["title"], "items": items,
                                               "spare_parts": g["spare_parts"], "suggested_interval_value": 1,
                                               "suggested_interval_unit": "month", "source_file_id": f["id"],
                                               "ai_suggestion_id": g["suggestion"]["id"]})
    assert status == 200, t
    assert t["status"] == "draft" and t["source_filename"] == "manual.pdf" and t["ai_suggestion_id"] == g["suggestion"]["id"]
    assert sup.post("/api/templates", {"kind": "pm", "code": "PM-TPX-M2", "title": "x", "items": items,
                                       "ai_suggestion_id": g["suggestion"]["id"]})[0] == 409   # a suggestion is used once
