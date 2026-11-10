"""Phase 5: forms and records — block layouts to PDF, Word templates with merge fields, approval, controlled copies."""
import io

import psycopg
import pytest

from conftest import sign_in_first_time

PW = "Forms#Admin2026x"


@pytest.fixture(scope="module")
def demo(server, platform):
    status, body, _ = platform.post("/api/platform/tenants", {"code": "FORMS", "name": "Forms Pharma",
                                                              "admin": {"email": "admin@forms.test", "full_name": "Forms Admin"}})
    assert status == 200, body
    admin = sign_in_first_time(server, "admin@forms.test", body["temporary_password"], PW)
    status, out, _ = admin.post("/api/sample-data", {"give_me_roles": True})
    assert status == 200 and out["forms"] == 6, out
    return {"admin": admin, "site_id": out["site_id"]}


def pdf_text(data: bytes) -> str:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(io.BytesIO(data))
    return "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))


def sig(**extra):
    return {"email": "admin@forms.test", "password": PW, **extra}


def test_print_controlled_copies(demo, db_url):
    a = demo["admin"]
    _, lst, _ = a.get("/api/forms")
    assert {f["source"] for f in lst["items"]} == {"calibration_plan", "pm_plan", "calibration_record", "pm_record", "history_card", "status_label"}
    assert all(f["status"] == "approved" and f["is_default"] for f in lst["items"])
    _, tasks, _ = a.get("/api/tasks?kind=calibration&state=completed")
    t = tasks["items"][0]
    status, data, headers = a.call("GET", f"/api/forms/print?source=calibration_record&task_id={t['id']}", raw=True)
    assert status == 200 and data[:4] == b"%PDF" and headers["X-Copy-Number"].startswith("CC-")
    text = pdf_text(data)
    assert t["number"] in text and t["asset_code"] in text and "Readings" in text and "QA027-00/F02" in text
    assert "Controlled copy" in text and "DRAFT" not in text and "Page 1 of 1" in text
    # annual plan in the QA027 layout (scheduled / executed rows)
    status, data, _ = a.call("GET", f"/api/forms/print?source=calibration_plan&site_id={demo['site_id']}&year=2026", raw=True)
    text = pdf_text(data)
    assert status == 200 and "Annual calibration schedule" in text and "THG-111" in text and "Authorized by (Head QA)" in text
    status, data, _ = a.call("GET", f"/api/forms/print?source=history_card&asset_id={t['asset_id']}", raw=True)
    assert status == 200 and "Equipment history card" in pdf_text(data)
    _, issues, _ = a.get(f"/api/forms/issues?record_id={t['id']}")
    assert len(issues["items"]) == 1 and issues["items"][0]["template_code"] == "QA027-F02" and len(issues["items"][0]["sha256"]) == 64
    with psycopg.connect(db_url, autocommit=True) as conn:
        with pytest.raises(psycopg.Error):
            conn.execute("DELETE FROM form_issues WHERE id = %s", (issues["items"][0]["id"],))


def test_block_designer_draft_approve_version(demo, db_url):
    a = demo["admin"]
    _, meta, _ = a.get("/api/forms/meta")
    layout = meta["defaults"]["pm_record"]
    layout["header"]["title"] = "PM work order {{task.number}}"
    status, f, _ = a.post("/api/forms", {"code": "pm-wo", "title": "PM work order", "source": "pm_record", "format_no": "EN002-01/F09",
                                         "revision": "00", "layout": layout})
    assert status == 200 and f["status"] == "draft" and f["code"] == "PM-WO"
    status, prev, _ = a.call("GET", f"/api/forms/{f['id']}/preview", raw=True)
    assert status == 200 and "DRAFT" in pdf_text(prev) and "PM work order PM-" in pdf_text(prev)
    status, live, _ = a.call("POST", "/api/forms/preview", {"source": "status_label", "layout": meta["defaults"]["status_label"]}, raw=True)
    assert status == 200 and live[:4] == b"%PDF"
    status, png, _ = a.post("/api/forms/preview?as=png", {"source": "calibration_plan", "layout": meta["defaults"]["calibration_plan"]})
    assert status == 200 and png["count"] >= 1 and png["pages"][0].startswith("data:image/png;base64,")
    status, f2, _ = a.patch(f"/api/forms/{f['id']}", {"revision": "01"})
    assert status == 200 and f2["revision"] == "01"
    status, f3, _ = a.post(f"/api/forms/{f['id']}/approve", sig())
    assert status == 200 and f3["status"] == "approved" and f3["signature"]["user_name"] == "Forms Admin"
    assert f3["is_default"] is False   # the demo form stays the default until changed
    assert a.patch(f"/api/forms/{f['id']}", {"title": "x"})[0] == 409
    with psycopg.connect(db_url, autocommit=True) as conn:
        with pytest.raises(psycopg.Error):
            conn.execute("UPDATE form_templates SET title = 'x' WHERE id = %s", (f["id"],))
    status, f4, _ = a.post(f"/api/forms/{f['id']}/default")
    assert status == 200 and f4["is_default"] is True
    status, nv, _ = a.post(f"/api/forms/{f['id']}/new-version")
    assert status == 200 and nv["version"] == 2 and nv["status"] == "draft"
    status, nv2, _ = a.post(f"/api/forms/{nv['id']}/approve", sig())
    assert nv2["status"] == "approved" and nv2["is_default"] is True   # the default follows the new version
    _, old, _ = a.get(f"/api/forms/{f['id']}")
    assert old["status"] == "superseded"


def test_word_template_merge(demo):
    import docx
    a = demo["admin"]
    status, starter, _ = a.call("GET", "/api/forms/starter.docx?source=pm_plan", raw=True)
    assert status == 200 and starter[:2] == b"PK"
    status, up = a.upload("EN002.docx", starter, "form")
    assert status == 200, up
    status, f, _ = a.post("/api/forms", {"code": "EN002-W", "title": "PM schedule (Word)", "source": "pm_plan", "engine": "docx",
                                         "file_id": up["id"], "format_no": "EN002-01/F01"})
    assert status == 200 and f["unknown"] == [] and "se_rows.asset_code" in f["placeholders"]
    status, out, h = a.call("GET", f"/api/forms/{f['id']}/print?site_id={demo['site_id']}&year=2026", raw=True)
    assert status == 200 and h["Content-Type"].startswith("application/vnd.openxmlformats")
    d = docx.Document(io.BytesIO(out))
    text = "\n".join(c.text for t in d.tables for r in t.rows for c in r.cells)
    assert "TP-01" in text and "{{" not in text
    big = max(d.tables, key=lambda t: len(t.rows))
    assert len(big.rows) > 40          # one S and one E row per schedule
    assert "DRAFT – NOT FOR GMP USE" in "\n".join(p.text for p in d.sections[0].header.paragraphs)
    # an unknown merge field blocks approval
    bad = docx.Document()
    bad.add_paragraph("Asset {{asset.code}} and {{asset.colour}}")
    buf = io.BytesIO()
    bad.save(buf)
    _, up2 = a.upload("bad.docx", buf.getvalue(), "form")
    _, f2, _ = a.post("/api/forms", {"code": "BAD-W", "title": "Bad", "source": "status_label", "engine": "docx", "file_id": up2["id"]})
    assert f2["unknown"] == ["asset.colour"]
    status, err, _ = a.post(f"/api/forms/{f2['id']}/approve", sig())
    assert status == 422 and err["error"]["problems"] == ["asset.colour"]
