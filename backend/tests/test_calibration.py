"""Calibration: tolerance settings, templates, schedules, tasks, signed completion, OOT handling, vendor flow, certificate AI."""
import datetime as dt
import io

import psycopg
import pytest

from conftest import sign_in_first_time

PW = {"cal": "Calibr8#Pass2026", "qa2": "QualityB#Pass2026", "sup": "Superv1#Pass2026"}
TODAY = dt.date.today()


def user(server, admin, email, name, roles, site_id, pw):
    status, body, _ = admin.post("/api/users", {"email": email, "full_name": name,
                                                "roles": [{"site_id": site_id, "role": r} for r in roles]})
    assert status == 200, body
    return sign_in_first_time(server, email, body["temporary_password"], pw)


def sign(client, email, pw, **extra):
    return {"email": email, "password": pw, **extra}


@pytest.fixture(scope="module")
def lab(server, two_companies):
    nile = two_companies["NILE"]
    admin, site = nile["admin"], nile["site"]
    cal = user(server, admin, "cal@nile.test", "Cal Officer", ["qa_calibration_officer"], site["id"], PW["cal"])
    qa = user(server, admin, "qa2@nile.test", "QA Two", ["qa_approver"], site["id"], PW["qa2"])
    sup = user(server, admin, "sup@nile.test", "Eng Supervisor", ["engineering_supervisor", "engineering_manager"], site["id"], PW["sup"])
    _, classes, _ = admin.get("/api/asset-classes")
    cls = {c["code"]: c["id"] for c in classes["items"]}
    assets = {}
    for code, klass, serial in (("BAL-A", "BALANCE", "SN-A"), ("BAL-B", "BALANCE", "SN-B"), ("BAL-EXT", "BALANCE", "SN-EXT-1"),
                                ("WTS-1", "WEIGHTS", "W-1"), ("PG-1", "PGAUGE", "PG-1")):
        status, a, _ = sup.post("/api/assets", {"site_id": site["id"], "code": code, "name_en": code, "class_id": cls[klass],
                                                "serial_no": serial, "range_min": 0, "range_max": 220, "unit": "g"})
        assert status == 200, a
        assets[code] = a
    status, out, _ = qa.post("/api/assets/status", {"asset_ids": [a["id"] for a in assets.values()], "status": "in_service",
                                                   "reason": "Released", **sign(qa, "qa2@nile.test", PW["qa2"])})
    assert status == 200, out
    status, v, _ = sup.post("/api/vendors", {"code": "NCS", "name": "Nile Calibration Services", "accreditation_no": "CL-000",
                                             "accreditation_body": "EGAC", "accreditation_expiry": str(TODAY + dt.timedelta(days=300))})
    assert status == 200, v
    return {"admin": admin, "site": site, "cal": cal, "qa": qa, "sup": sup, "assets": assets, "vendor": v, "cls": cls}


def test_tolerance_settings(lab):
    admin = lab["admin"]
    _, s, _ = admin.get("/api/settings/work")
    bands = s["settings"]["tolerance"]["calibration"]
    assert bands[1] == {"up_to_days": 31, "before_value": None, "before_unit": "any", "after_value": 3, "after_unit": "day"}
    bad = [dict(b, up_to_days=31) for b in bands[:2]]
    status, err, _ = admin.put("/api/settings/work", {"calibration": bad})
    assert status == 422
    new = [dict(b) for b in bands]
    new[-1] = {"up_to_days": None, "before_value": 5, "before_unit": "percent", "after_value": 5, "after_unit": "percent"}
    status, s2, _ = admin.put("/api/settings/work", {"calibration": new, "due_soon_days": 21})
    assert status == 200 and s2["settings"]["due_soon_days"] == 21
    status, p, _ = admin.post("/api/schedules/preview", {"interval_value": 2, "interval_unit": "year", "first_due": "2027-01-01"})
    assert status == 200
    d0 = p["dates"][0]
    assert d0["window_start"] == "2026-11-26" and d0["window_end"] == "2027-02-06"   # ±5 % of 730.5 days = 36 days
    status, p, _ = admin.post("/api/schedules/preview", {"interval_value": 1, "interval_unit": "month", "first_due": "2027-01-31"})
    assert p["dates"][0]["window_start"] is None and p["dates"][0]["window_end"] == "2027-02-03"
    assert p["dates"][1]["due"] == "2027-02-28"  # month end is kept inside the month
    # schedule's own asymmetric rule wins over the band
    status, p, _ = admin.post("/api/schedules/preview", {"interval_value": 1, "interval_unit": "year", "first_due": "2027-06-15",
                                                         "tol_before_unit": "month", "tol_before_value": 1,
                                                         "tol_after_unit": "week", "tol_after_value": 2})
    assert p["dates"][0]["window_start"] == "2027-05-15" and p["dates"][0]["window_end"] == "2027-06-29"
    assert admin.put("/api/settings/work", {"calibration": bands, "due_soon_days": 30})[0] == 200


def test_templates_approval_and_versions(lab, db_url):
    sup, qa, cal = lab["sup"], lab["qa"], lab["cal"]
    body = {"code": "cal-bal", "title": "Balance calibration 220 g", "class_id": lab["cls"]["BALANCE"], "method_ref": "SOP-QC-010",
            "items": [{"parameter": "Mass", "nominal": 100, "unit": "g", "tol_mode": "abs", "tol_plus": 0.0005},
                      {"parameter": "Mass", "nominal": 200, "unit": "g", "tol_mode": "pct", "tol_plus": 0.0005},
                      {"parameter": "Eccentricity", "nominal": 100, "unit": "g", "tol_mode": "limits", "low_limit": 99.999, "high_limit": 100.001}]}
    status, t, _ = sup.post("/api/templates", body)
    assert status == 200, t
    assert t["code"] == "CAL-BAL" and t["status"] == "draft" and t["can_approve"] is False
    assert float(t["items"][0]["tol_minus"]) == 0.0005  # symmetric when only one side is given
    assert float(t["items"][1]["limit_low"]) == pytest.approx(199.999) and float(t["items"][1]["limit_high"]) == pytest.approx(200.001)
    status, err, _ = sup.post("/api/templates", {**body, "code": "X", "items": [{"parameter": "Mass", "unit": "g", "tol_mode": "abs", "tol_plus": 1}]})
    assert status == 422
    status, err, _ = sup.post(f"/api/templates/{t['id']}/approve", sign(sup, "sup@nile.test", PW["sup"]))
    assert status == 403
    status, t1, _ = qa.post(f"/api/templates/{t['id']}/approve", sign(qa, "qa2@nile.test", PW["qa2"]))
    assert status == 200 and t1["status"] == "approved" and t1["signatures"][0]["meaning"] == "template_approved"
    status, err, _ = sup.patch(f"/api/templates/{t['id']}", {"title": "changed"})
    assert status == 409
    conn = psycopg.connect(db_url)
    with pytest.raises(psycopg.Error):
        conn.execute("UPDATE template_items SET nominal = 1 WHERE template_id = %s", (t["id"],))
    conn.rollback()
    conn.close()
    status, t2, _ = sup.post(f"/api/templates/{t['id']}/new-version")
    assert status == 200 and t2["version"] == 2 and len(t2["items"]) == 3
    status, err, _ = sup.post(f"/api/templates/{t['id']}/new-version")
    assert status == 409
    lab["template"] = t1
    lab["template_v2"] = t2


def test_schedules_and_internal_calibration(lab, db_url):
    sup, cal, qa, A = lab["sup"], lab["cal"], lab["qa"], lab["assets"]
    # the reference standard itself is calibrated (valid until next year)
    status, out, _ = sup.post("/api/schedules", {"asset_ids": [A["WTS-1"]["id"]], "interval_value": 1, "interval_unit": "year",
                                                 "performer": "external", "vendor_id": lab["vendor"]["id"],
                                                 "first_due": str(TODAY + dt.timedelta(days=200))})
    assert status == 200 and len(out["created"]) == 1, out
    status, out, _ = sup.post("/api/schedules", {"asset_ids": [A["BAL-A"]["id"], A["BAL-B"]["id"]], "interval_value": 12,
                                                 "interval_unit": "month", "template_id": lab["template"]["id"],
                                                 "assignee_roles": ["qa_calibration_officer"],
                                                 "first_due": str(TODAY + dt.timedelta(days=5)), "stagger_days": 7})
    assert status == 200, out
    dues = [c["task"]["due_date"] for c in out["created"]]
    assert dues == [str(TODAY + dt.timedelta(days=5)), str(TODAY + dt.timedelta(days=12))]
    status, again, _ = sup.post("/api/schedules", {"asset_ids": [A["BAL-A"]["id"]], "interval_value": 6, "interval_unit": "month"})
    assert again["created"] == [] and again["skipped"][0]["reason"] == "exists"
    tid = out["created"][0]["task"]["id"]
    _, t, _ = cal.get(f"/api/tasks/{tid}")
    assert t["number"].startswith("CAL-") and t["window_start"] is None and len(t["results"]) == 3
    assert t["needs_acceptance"] is True and t["perms"]["accept"] is True and t["perms"]["edit"] is False
    wts = [s for s in t["standard_options"] if s["code"] == "WTS-1"][0]
    assert wts["usable"] is True
    results = {"performed_date": str(TODAY), "env_temperature": "21 °C",
               "results": [{"item_id": r["item_id"], "parameter": r["parameter"], "as_found": float(r["nominal"]) + 0.0001}
                           for r in t["results"]]}
    status, err, _ = cal.put(f"/api/tasks/{tid}/results", results)
    assert status == 409 and err["error"]["code"] == "not_accepted"
    assert cal.post(f"/api/tasks/{tid}/accept")[0] == 200
    status, t, _ = cal.put(f"/api/tasks/{tid}/results", results)
    assert status == 200 and t["status"] == "in_progress" and t["verdict_preview"] == "pass"
    status, err, _ = cal.post(f"/api/tasks/{tid}/complete", sign(cal, "cal@nile.test", PW["cal"]))
    assert status == 422 and "standards" in err["error"]["problems"]
    status, t, _ = cal.put(f"/api/tasks/{tid}/results", {**results, "standards": [{"standard_asset_id": A["WTS-1"]["id"]}]})
    assert t["standards"][0]["description"].startswith("WTS-1")
    status, err, _ = cal.post(f"/api/tasks/{tid}/complete", sign(cal, "cal@nile.test", "wrong"))
    assert status == 403
    status, done, _ = cal.post(f"/api/tasks/{tid}/complete", sign(cal, "cal@nile.test", PW["cal"]))
    assert status == 200, done
    assert done["status"] == "completed" and done["verdict"] == "pass" and done["signature"]["meaning"] == "calibration_completed"
    nxt = done["next_task"]
    assert nxt["due_date"] == str(TODAY.replace(year=TODAY.year + 1)) if not (TODAY.month == 2 and TODAY.day == 29) else True
    # closed record cannot change
    status, err, _ = cal.put(f"/api/tasks/{tid}/results", results)
    assert status == 409
    conn = psycopg.connect(db_url)
    with pytest.raises(psycopg.Error):
        conn.execute("UPDATE task_results SET as_found = 1 WHERE task_id = %s", (tid,))
    conn.rollback()
    with pytest.raises(psycopg.Error):
        conn.execute("UPDATE tasks SET verdict = 'fail' WHERE id = %s", (tid,))
    conn.rollback()
    conn.close()
    # review: four eyes
    status, err, _ = cal.post(f"/api/tasks/{tid}/review", sign(cal, "cal@nile.test", PW["cal"]))
    assert status == 403
    status, rv, _ = qa.post(f"/api/tasks/{tid}/review", sign(qa, "qa2@nile.test", PW["qa2"]))
    assert status == 200 and rv["review_signature"]["meaning"] == "calibration_reviewed"
    lab["bal_b_task"] = out["created"][1]["task"]["id"]


def test_oot_adjusted_and_fail(lab):
    cal, qa, A = lab["cal"], lab["qa"], lab["assets"]
    tid = lab["bal_b_task"]
    cal.post(f"/api/tasks/{tid}/accept")
    _, t, _ = cal.get(f"/api/tasks/{tid}")
    rows = [{"item_id": r["item_id"], "parameter": r["parameter"], "as_found": float(r["nominal"]) + (0.002 if r["seq"] == 1 else 0),
             "as_left": float(r["nominal"]) if r["seq"] == 1 else None} for r in t["results"]]
    status, t, _ = cal.put(f"/api/tasks/{tid}/results", {"performed_date": str(TODAY), "results": rows,
                                                         "standards": [{"standard_asset_id": A["WTS-1"]["id"]}]})
    assert t["verdict_preview"] == "adjusted" and t["results"][0]["ok_found"] is False and t["results"][0]["ok_left"] is True
    status, done, _ = cal.post(f"/api/tasks/{tid}/complete", sign(cal, "cal@nile.test", PW["cal"]))
    assert status == 200 and done["verdict"] == "adjusted" and done["impact_created"]["number"].startswith("IA-")
    _, a, _ = cal.get(f"/api/assets/{A['BAL-B']['id']}")
    assert a["status"] == "in_service"  # adjusted back into tolerance: usable, past use assessed
    iid = done["impact_created"]["id"]
    status, d, _ = qa.post(f"/api/impacts/{iid}/ai-draft")
    assert status == 200 and d["fields"]["risk"] == "medium" and d["fields"]["actions"].startswith("1. Quarantine")
    status, err, _ = qa.post(f"/api/impacts/{iid}/approve", sign(qa, "qa2@nile.test", PW["qa2"]))
    assert status == 422
    status, i, _ = qa.patch(f"/api/impacts/{iid}", {**{k: d["fields"][k] for k in ("summary", "scope", "products", "actions", "risk")},
                                                     "conclusion": "No impact: all assays within limits after recalculation.",
                                                     "ai_suggestion_id": d["suggestion"]["id"]})
    assert status == 200 and i["ai"]["status"] == "accepted" and list(i["ai"]["changed_fields"]) == ["conclusion"]
    status, err, _ = cal.post(f"/api/impacts/{iid}/approve", sign(cal, "cal@nile.test", PW["cal"]))
    assert status == 403
    status, i, _ = qa.post(f"/api/impacts/{iid}/approve", sign(qa, "qa2@nile.test", PW["qa2"]))
    assert status == 200 and i["status"] == "approved"
    assert qa.patch(f"/api/impacts/{iid}", {"summary": "x"})[0] == 403
    # the next one fails: as left still out -> quarantined under the completion signature
    nxt = done["next_task"]["id"]
    cal.post(f"/api/tasks/{nxt}/accept")
    _, t, _ = cal.get(f"/api/tasks/{nxt}")
    rows = [{"item_id": r["item_id"], "parameter": r["parameter"], "as_found": float(r["nominal"]) + 0.01, "as_left": float(r["nominal"]) + 0.01}
            for r in t["results"]]
    cal.put(f"/api/tasks/{nxt}/results", {"performed_date": str(TODAY), "results": rows, "standards": [{"standard_asset_id": A["WTS-1"]["id"]}]})
    status, done2, _ = cal.post(f"/api/tasks/{nxt}/complete", sign(cal, "cal@nile.test", PW["cal"]))
    assert status == 200 and done2["verdict"] == "fail"
    _, a, _ = cal.get(f"/api/assets/{A['BAL-B']['id']}")
    assert a["status"] == "quarantined" and a["history"][0]["meaning"] == "calibration_completed"
    _, lst, _ = cal.get(f"/api/tasks?state=oot")
    assert lst["total"] == 2


def test_external_vendor_with_certificate_ai(lab):
    sup, cal, A = lab["sup"], lab["cal"], lab["assets"]
    status, out, _ = sup.post("/api/schedules", {"asset_ids": [A["BAL-EXT"]["id"]], "interval_value": 1, "interval_unit": "year",
                                                 "template_id": lab["template"]["id"], "performer": "external",
                                                 "vendor_id": lab["vendor"]["id"], "responsible_user_id": None,
                                                 "first_due": str(TODAY + dt.timedelta(days=3))})
    assert status == 200, out
    tid = out["created"][0]["task"]["id"]
    _, t, _ = cal.get(f"/api/tasks/{tid}")
    assert t["certificate_required"] is True and t["needs_acceptance"] is False and t["perms"]["send"] is True
    status, t, _ = cal.post(f"/api/tasks/{tid}/send", sign(cal, "cal@nile.test", PW["cal"]))
    assert status == 200, t
    assert t["status"] == "at_vendor" and t["asset"]["status"] == "out_for_calibration"
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (600, 800), "white").save(buf, "PNG")
    status, f = cal.upload("certificate.png", buf.getvalue(), "certificate")
    assert status == 200
    status, ai, _ = cal.post(f"/api/tasks/{tid}/certificate", {"file_id": f["id"]})
    assert status == 200, ai
    checks = {c["key"]: c["status"] for c in ai["checks"]}
    assert checks["serial"] == "ok" and checks["coverage"] == "fail" and checks["tolerance"] == "ok"
    assert checks["accreditation"] == "ok" and checks["standards"] == "ok" and checks["conformity"] == "ok"
    m = {x["seq"]: x for x in ai["mapped"]}
    assert m[1]["as_found"] == pytest.approx(100.0004) and m[3].get("missing") is True and len(ai["extra_readings"]) == 1
    # the officer enters the eccentricity point by hand and saves from the suggestion
    rows = [{"item_id": r["item_id"], "parameter": r["parameter"],
             "as_found": m[r["seq"]]["as_found"] if not m[r["seq"]].get("missing") else 100.0003,
             "uncertainty": m[r["seq"]].get("uncertainty")} for r in t["results"]]
    status, t, _ = cal.put(f"/api/tasks/{tid}/results", {"performed_date": ai["fields"]["performed_date"],
                                                         "certificate_no": ai["fields"]["certificate_no"],
                                                         "certificate_file_id": f["id"],
                                                         "env_temperature": ai["fields"]["env_temperature"],
                                                         "env_humidity": ai["fields"]["env_humidity"], "results": rows})
    assert status == 200, t
    status, done, _ = cal.post(f"/api/tasks/{tid}/complete", sign(cal, "cal@nile.test", PW["cal"], ai_suggestion_id=ai["suggestion"]["id"]))
    assert status == 200, done
    assert done["verdict"] == "pass" and done["asset"]["status"] == "in_service"
    assert done["ai"]["status"] == "accepted" and set(done["ai"]["changed_fields"]) == {"p3.as_found"}


def test_cancel_overdue_and_counts(lab):
    sup, A = lab["sup"], lab["assets"]
    status, out, _ = sup.post("/api/schedules", {"asset_ids": [A["PG-1"]["id"]], "interval_value": 6, "interval_unit": "month",
                                                 "first_due": str(TODAY - dt.timedelta(days=40))})
    tid = out["created"][0]["task"]["id"]
    _, lst, _ = sup.get(f"/api/tasks?state=overdue&site_id={lab['site']['id']}")
    assert [x["asset_code"] for x in lst["items"]] == ["PG-1"] and lst["counts"]["overdue"] == 1
    _, tree, _ = sup.get("/api/tree")
    site = [s for s in tree["sites"] if s["id"] == lab["site"]["id"]][0]
    assert site["counts"]["work"]["calibration"]["overdue"] == 1 and site["counts"]["work"]["calibration"]["open"] >= 4
    _, assets, _ = sup.get(f"/api/assets?q=PG-1&site_id={lab['site']['id']}")
    assert assets["items"][0]["cal_overdue"] is True
    status, c, _ = sup.post(f"/api/tasks/{tid}/cancel", sign(sup, "sup@nile.test", PW["sup"], reason="Gauge replaced by new one"))
    assert status == 200 and c["status"] == "cancelled" and c["next_task"]["due_date"] > str(TODAY)
    _, work, _ = sup.get(f"/api/assets/{A['PG-1']['id']}/work")
    assert len(work["schedules"]) == 1 and {t["status"] for t in work["tasks"]} == {"cancelled", "open"}
    status, _, _ = sup.post(f"/api/schedules/{work['schedules'][0]['id']}/end", {"reason": "Gauge removed"})
    _, work, _ = sup.get(f"/api/assets/{A['PG-1']['id']}/work")
    assert work["schedules"][0]["active"] is False and {t["status"] for t in work["tasks"]} == {"cancelled"}
    status, raw, _ = sup.get(f"/api/assets/labels.pdf?ids={A['BAL-A']['id']}", raw=True)
    assert status == 200 and raw[:4] == b"%PDF"
