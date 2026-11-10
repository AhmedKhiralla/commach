"""Asset register: hierarchy, lists, assets, signed status changes, files, AI, import, labels, demo data, integration."""
import io
import json

import psycopg
import pytest

from conftest import Client, sign_in_first_time

PW = {"eng": "Engineer#Pass2026", "qa": "QualityA#Pass2026", "tech": "Technic#Pass2026"}


def make_user(server, admin, email, name, roles, site_id, key):
    status, body, _ = admin.post("/api/users", {"email": email, "full_name": name,
                                                "roles": [{"site_id": site_id, "role": r} for r in roles]})
    assert status == 200, body
    return sign_in_first_time(server, email, body["temporary_password"], PW[key])


@pytest.fixture(scope="module")
def plant(server, two_companies):
    acme = two_companies["ACME"]
    admin, site = acme["admin"], acme["site"]
    eng = make_user(server, admin, "eng@acme.test", "Eng Supervisor", ["engineering_supervisor"], site["id"], "eng")
    qa = make_user(server, admin, "qa@acme.test", "QA Approver", ["qa_approver"], site["id"], "qa")
    tech = make_user(server, admin, "tech@acme.test", "Technician", ["maintenance_technician"], site["id"], "tech")
    _, classes, _ = admin.get("/api/asset-classes")
    cls = {c["code"]: c for c in classes["items"]}
    return {"admin": admin, "site": site, "eng": eng, "qa": qa, "tech": tech, "cls": cls, "acme": acme}


def test_default_classes_seeded(plant):
    assert {"BALANCE", "PGAUGE", "DPGAUGE", "SENSOR", "REFSTD", "AHU", "FACILITY"} <= set(plant["cls"])
    assert plant["cls"]["BALANCE"]["calibration_default"] is True and plant["cls"]["AHU"]["pm_default"] is True


def test_hierarchy_rules(plant):
    eng, site, admin = plant["eng"], plant["site"], plant["admin"]
    status, g, _ = admin.post("/api/site-groups", {"code": "eg", "name": "Egypt"})
    assert status == 200 and g["code"] == "EG"
    status, s, _ = admin.patch(f"/api/sites/{site['id']}", {"group_id": g["id"]})
    assert status == 200 and s["group_name"] == "Egypt"
    status, b, _ = eng.post(f"/api/sites/{site['id']}/locations", {"kind": "building", "code": "B1", "name_en": "OSD"})
    assert status == 200, b
    status, room, _ = eng.post(f"/api/sites/{site['id']}/locations",
                               {"kind": "room", "code": "R-101", "name_en": "Dispensing", "parent_id": b["id"], "cleanroom_grade": "D"})
    assert status == 200, room
    # a building cannot sit inside a room
    status, err, _ = eng.post(f"/api/sites/{site['id']}/locations", {"kind": "building", "code": "B9", "name_en": "X", "parent_id": room["id"]})
    assert status == 422
    # a technician cannot change the register
    status, _, _ = plant["tech"].post(f"/api/sites/{site['id']}/locations", {"kind": "room", "code": "R-9", "name_en": "X"})
    assert status == 403
    status, line, _ = eng.post(f"/api/sites/{site['id']}/lines", {"code": "L1", "name_en": "Tablet line", "location_id": b["id"]})
    assert status == 200, line
    # rename needs a reason for change, stored in the audit trail
    status, err, _ = eng.patch(f"/api/locations/{room['id']}", {"name_en": "Dispensing booth"})
    assert status == 422 and err["error"]["code"] == "reason_required"
    status, room2, _ = eng.patch(f"/api/locations/{room['id']}", {"name_en": "Dispensing booth", "reason": "Name per new layout"})
    assert status == 200 and room2["name_en"] == "Dispensing booth"
    _, audit, _ = admin.get(f"/api/audit?record_id={room['id']}&entity=locations")
    upd = [e for e in audit["items"] if e["action"] == "UPDATE"][0]
    assert upd["details"]["reason"] == "Name per new layout" and upd["new_values"]["name_en"] == "Dispensing booth"
    plant.update(building=b, room=room, line=line)


def test_vendor_with_contacts(plant):
    eng = plant["eng"]
    status, v, _ = eng.post("/api/vendors", {"code": "ncs", "name": "Nile Calibration", "accreditation_body": "EGAC",
                                             "accreditation_no": "CL-1", "accreditation_expiry": "2020-01-01",
                                             "contacts": [{"name": "Mona", "email": "Mona@NCS.example", "is_primary": True}]})
    assert status == 200, v
    assert v["code"] == "NCS" and v["accreditation_expired"] is True and v["contacts"][0]["email"] == "mona@ncs.example"
    status, v2, _ = eng.patch(f"/api/vendors/{v['id']}", {"contacts": [{"name": "Karim", "is_primary": True}],
                                                           "accreditation_expiry": "2030-01-01", "reason": "Renewed"})
    assert status == 200, v2
    assert v2["accreditation_expired"] is False
    assert {c["name"]: c["active"] for c in v2["contacts"]} == {"Karim": True, "Mona": False}
    plant["vendor"] = v


def test_asset_create_and_components(plant):
    eng, site, cls = plant["eng"], plant["site"], plant["cls"]
    status, m, _ = eng.post("/api/assets", {"site_id": site["id"], "code": "TP-01", "name_en": "Tablet press",
                                            "class_id": cls["MACHINE"]["id"], "location_id": plant["room"]["id"],
                                            "line_id": plant["line"]["id"], "gmp_criticality": "critical"})
    assert status == 200, m
    assert m["status"] == "draft" and m["pm_required"] is True and m["calibration_required"] is False
    assert [p["code"] for p in m["location_path"]] == ["B1", "R-101"]
    status, s, _ = eng.post("/api/assets", {"site_id": site["id"], "code": "TP-01-FS", "name_en": "Force sensor",
                                            "class_id": cls["SENSOR"]["id"], "parent_id": m["id"],
                                            "range_min": 0, "range_max": 100, "unit": "kN"})
    assert status == 200 and s["calibration_required"] is True and s["parent_code"] == "TP-01"
    status, err, _ = eng.post("/api/assets", {"site_id": site["id"], "code": "tp-01", "name_en": "dup", "class_id": cls["MACHINE"]["id"]})
    assert status == 422 and "code" in err["error"]["fields"]
    status, err, _ = eng.post("/api/assets", {"site_id": site["id"], "code": "X1", "name_en": "bad range",
                                              "class_id": cls["SENSOR"]["id"], "range_min": 10, "range_max": 1})
    assert status == 422
    # no cycles: the press cannot become a component of its own sensor
    status, err, _ = eng.patch(f"/api/assets/{m['id']}", {"parent_id": s["id"], "reason": "test"})
    assert status == 422 and "parent_id" in err["error"]["fields"]
    status, m2, _ = eng.get(f"/api/assets/{m['id']}")
    assert [c["code"] for c in m2["children"]] == ["TP-01-FS"]
    plant.update(press=m, sensor=s)


def test_status_needs_signature_and_role(plant, db_url):
    eng, qa, press = plant["eng"], plant["qa"], plant["press"]
    # the database itself refuses a status change that is not signed
    conn = psycopg.connect(db_url)
    conn.execute("SET LOCAL ROLE commach_app")
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (plant["acme"]["tenant"]["id"],))
    with pytest.raises(psycopg.Error):
        conn.execute("UPDATE assets SET status = 'in_service' WHERE id = %s", (press["id"],))
    conn.rollback()
    conn.close()
    # an engineering supervisor cannot approve into service
    status, err, _ = eng.post("/api/assets/status", {"asset_ids": [press["id"]], "status": "in_service", "reason": "IQ/OQ done",
                                                    "email": "eng@acme.test", "password": PW["eng"]})
    assert status == 422 and err["error"]["problems"][0]["problem"] == "role"
    # wrong password: nothing signed, failure audited
    status, err, _ = qa.post("/api/assets/status", {"asset_ids": [press["id"]], "status": "in_service", "reason": "IQ/OQ done",
                                                   "email": "qa@acme.test", "password": "wrong"})
    assert status == 403 and err["error"]["code"] == "bad_signature"
    status, out, _ = qa.post("/api/assets/status", {"asset_ids": [press["id"], plant["sensor"]["id"]], "status": "in_service",
                                                   "reason": "Qualification approved", "email": "qa@acme.test", "password": PW["qa"]})
    assert status == 200, out
    assert {c["to"] for c in out["changed"]} == {"in_service"} and len({c["signature_id"] for c in out["changed"]}) == 2
    _, a, _ = qa.get(f"/api/assets/{press['id']}")
    h = a["history"][0]
    assert a["status"] == "in_service" and h["meaning"] == "in_service" and h["user_email"] == "qa@acme.test"
    assert len(h["record_hash"]) == 64 and "approve this asset into service" in h["statement"]
    # a technician can take a machine out of service (breakdown), but not release it back
    status, out, _ = plant["tech"].post("/api/assets/status", {"asset_ids": [press["id"]], "status": "out_of_service",
                                                              "reason": "Breakdown", "note": "Belt broken",
                                                              "email": "tech@acme.test", "password": PW["tech"]})
    assert status == 200, out
    _, opts, _ = plant["tech"].get(f"/api/assets/status-options?ids={press['id']}")
    assert "in_service" not in opts["statuses"]
    _, audit, _ = plant["admin"].get("/api/audit?action=SIGNATURE_FAILED")
    assert audit["total"] >= 1


def test_tree_counts(plant):
    _, tree, _ = plant["eng"].get("/api/tree")
    site = [s for s in tree["sites"] if s["id"] == plant["site"]["id"]][0]
    assert site["counts"]["total"] == 2 and site["counts"]["calibration"] == 1 and site["counts"]["unavailable"] == 1
    b1 = site["nodes"][0]
    assert b1["code"] == "B1" and b1["counts"]["total"] == 1 and b1["children"][0]["counts"]["total"] == 1
    assert tree["groups"][0]["counts"]["total"] == 2


def test_files_and_nameplate_ai(plant):
    from PIL import Image
    eng, site, cls = plant["eng"], plant["site"], plant["cls"]
    buf = io.BytesIO()
    Image.new("RGB", (400, 200), "white").save(buf, "PNG")
    status, f = eng.upload("nameplate.png", buf.getvalue(), "nameplate")
    assert status == 200 and f["mime"] == "image/png", f
    status, bad = eng.upload("note.txt", b"hello", "attachment")
    assert status == 422
    status, ai, _ = eng.post("/api/ai/nameplate", {"file_id": f["id"], "hint": "balance"})
    assert status == 200, ai
    assert ai["fields"]["serial_no"] == "B123456789" and ai["class_id"] == cls["BALANCE"]["id"]
    # the person corrects the serial number and creates the asset from the suggestion
    status, a, _ = eng.post("/api/assets", {"site_id": site["id"], "code": "BAL-01", "name_en": ai["fields"]["name_en"],
                                            "class_id": ai["class_id"], "manufacturer": "Mettler Toledo", "model": "XPR205",
                                            "serial_no": "B123456780", "range_min": 0, "range_max": 220, "unit": "g",
                                            "name_ar": "ميزان تحليلي", "capacity": "220 g", "resolution": "d = 0.01 mg",
                                            "nameplate_file_id": f["id"], "ai_suggestion_id": ai["suggestion"]["id"]})
    assert status == 200, a
    assert a["ai"][0]["status"] == "accepted" and set(a["ai"][0]["changed_fields"]) == {"serial_no"}
    assert a["files"][0]["kind"] == "nameplate"
    # a suggestion is reviewed only once
    status, err, _ = eng.post("/api/assets", {"site_id": site["id"], "code": "BAL-02", "name_en": "x", "class_id": ai["class_id"],
                                              "ai_suggestion_id": ai["suggestion"]["id"]})
    assert status == 409
    _, audit, _ = plant["admin"].get("/api/audit?action=AI_SUGGESTED")
    assert audit["total"] >= 1
    status, raw, _ = eng.get(f"/api/files/{f['id']}/content", raw=True)
    assert status == 200 and raw[:4] == b"\x89PNG"


def _xlsx(rows):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Equipment list"
    ws.append(["ACME equipment list 2026"])
    ws.append([])
    for r in rows:
        ws.append(r)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_import_customer_sheet_with_ai(plant):
    eng, site = plant["eng"], plant["site"]
    data = _xlsx([["Equip. ID", "Description", "Type", "Make", "S/N", "Location", "Measuring range", "Condition"],
                  ["DPG-01", "Room DP gauge", "Magnehelic", "Dwyer", "111", "R-101", "0-60 Pa", "OK"],
                  ["BAL-77", "Balance 220 g", "Balance", "Sartorius", "222", "QC lab", "0 - 220 g", "OK"],
                  ["PG-05", "Air pressure gauge", "P. gauge", "Wika", "333", "QC lab", "0 to 10 bar", "Under repair"]])
    status, f = eng.upload("equipment.xlsx", data, "import")
    assert status == 200, f
    status, p, _ = eng.post("/api/assets/import/preview", {"file_id": f["id"], "site_id": site["id"]})
    assert status == 200, p
    assert p["header_row"] == 3 and p["mapping"]["S/N"] == "serial_no" and p["mapping"]["Make"] == "manufacturer"
    status, p, _ = eng.post("/api/assets/import/preview", {"file_id": f["id"], "site_id": site["id"], "use_ai": True})
    assert status == 200, p
    assert p["mapping"]["Equip. ID"] == "code" and p["classes"]["Magnehelic"] == "DPGAUGE" and "Weird" not in p["classes"]
    assert p["summary"] == {"rows": 3, "ok": 3, "errors": 0, "new_locations": ["room: QC lab"]}
    pg = p["rows"][2]["values"]
    assert pg["range_min"] == 0 and pg["range_max"] == 10 and pg["unit"] == "bar" and pg["intended_status"] == "out_of_service"
    status, out, _ = eng.post("/api/assets/import/commit", {**{k: p[k] for k in ("sheet", "header_row", "mapping", "classes", "status")},
                                                            "file_id": f["id"], "site_id": site["id"],
                                                            "suggestion_id": p["ai"]["suggestion_id"]})
    assert status == 200, out
    assert len(out["created"]) == 3 and out["locations_created"] == 1
    _, lst, _ = eng.get(f"/api/assets?site_id={site['id']}&q=DPG-01")
    assert lst["items"][0]["status"] == "draft" and lst["items"][0]["location_code"] == "R-101"
    # importing the same sheet again is refused row by row (codes exist)
    status, err, _ = eng.post("/api/assets/import/commit", {**{k: p[k] for k in ("sheet", "header_row", "mapping", "classes", "status")},
                                                            "file_id": f["id"], "site_id": site["id"]})
    assert status == 422 and err["error"]["code"] == "import_errors"


def test_template_export_and_labels(plant):
    eng = plant["eng"]
    status, raw, h = eng.get("/api/assets/template.xlsx", raw=True)
    assert status == 200 and raw[:2] == b"PK"
    status, raw, _ = eng.get(f"/api/assets/export.xlsx?site_id={plant['site']['id']}", raw=True)
    assert status == 200 and raw[:2] == b"PK"
    status, raw, h = eng.get(f"/api/assets/labels.pdf?ids={plant['press']['id']},{plant['sensor']['id']}", raw=True)
    assert status == 200 and raw[:4] == b"%PDF" and h["content-type"] == "application/pdf"
    status, raw, _ = eng.get(f"/api/assets/labels.pdf?ids={plant['press']['id']}&layout=roll&size=small", raw=True)
    assert status == 200 and raw[:4] == b"%PDF"
    status, found, _ = eng.get("/api/assets/by-code/tp-01")
    assert status == 200 and found["id"] == plant["press"]["id"]


def test_other_company_cannot_see_assets(plant, two_companies):
    nile = two_companies["NILE"]["admin"]
    status, _, _ = nile.get(f"/api/assets/{plant['press']['id']}")
    assert status == 404
    _, lst, _ = nile.get("/api/assets")
    assert lst["total"] == 0


def test_integration_api(plant, server):
    admin = plant["admin"]
    status, k, _ = admin.post("/api/integration/keys", {"name": "Complanning"})
    assert status == 200 and k["key"].startswith("cmk_")
    import urllib.request
    req = urllib.request.Request(server + "/api/integration/v1/events?after=0", headers={"Authorization": f"Bearer {k['key']}"})
    with urllib.request.urlopen(req) as r:
        ev = json.loads(r.read())
    kinds = [e["event"] for e in ev["items"]]
    assert "asset.created" in kinds and "asset.status_changed" in kinds
    changed = [e for e in ev["items"] if e["event"] == "asset.status_changed" and e["payload"]["code"] == "TP-01"][-1]
    assert changed["payload"]["available"] is False and changed["payload"]["external_ref"] is None
    assert admin.post(f"/api/integration/keys/{k['id']}/revoke")[0] == 200
    req = urllib.request.Request(server + "/api/integration/v1/assets", headers={"Authorization": f"Bearer {k['key']}"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 401


def test_sample_data(server, platform):
    status, body, _ = platform.post("/api/platform/tenants", {"code": "C11DEMO", "name": "Comp11 Labs – Demo",
                                                              "admin": {"email": "demo@comp11.test", "full_name": "Demo Admin"}})
    assert status == 200, body
    admin = sign_in_first_time(server, "demo@comp11.test", body["temporary_password"], "Demo#Admin2026x")
    status, out, _ = admin.post("/api/sample-data", {"give_me_roles": True})
    assert status == 200, out
    assert out["assets"] > 120
    _, tree, _ = admin.get("/api/tree")
    site = tree["sites"][0]
    assert site["counts"]["total"] == out["assets"] - 1  # the retired oven is not counted
    assert site["counts"]["draft"] == 4 and site["counts"]["unavailable"] == 5
    _, lst, _ = admin.get("/api/assets?q=HPLC-01&top=1")
    assert lst["items"][0]["child_count"] == 4
    status, again, _ = admin.post("/api/sample-data", {})
    assert status == 409
    # the demo admin was given signing roles and can approve the drafts with one signature
    _, drafts, _ = admin.get("/api/assets?status=draft")
    status, res, _ = admin.post("/api/assets/status", {"asset_ids": [d["id"] for d in drafts["items"]], "status": "in_service",
                                                       "reason": "Registration checked", "email": "demo@comp11.test",
                                                       "password": "Demo#Admin2026x"})
    assert status == 200 and len(res["changed"]) == 4
