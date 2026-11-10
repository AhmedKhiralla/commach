"""The annual plan as a printable grid: one row per schedule, one column per month, with the review and approval
signatures at the foot (the format of a paper annual calibration / PM plan)."""
from __future__ import annotations

import datetime as dt
import io

from .labels import _fit, _font

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
UNIT = {"day": "d", "week": "wk", "month": "mo", "year": "yr"}


def render(v: dict, site: dict, company: str, today: dt.date) -> bytes:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    reg, bold = _font()
    W, H = landscape(A4)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(W, H))
    title = f"{'Calibration' if v['kind'] == 'calibration' else 'Preventive maintenance'} plan {v['year']}"
    m = 12 * mm
    name_w = 92 * mm
    col_w = (W - 2 * m - name_w) / 12
    row_h = 6.2 * mm
    pages: list[list[dict]] = []
    per_page = int((H - 2 * m - 46 * mm) // row_h)
    rows = v["rows"]
    for i in range(0, max(1, len(rows)), per_page):
        pages.append(rows[i:i + per_page])

    def head(page_no: int):
        c.setFont(bold, 13)
        c.drawString(m, H - m - 4, title)
        c.setFont(reg, 8.5)
        c.drawString(m, H - m - 16, f"{company} · {site['code']} {site['name']}")
        status = {"draft": "DRAFT – not approved", "reviewed": "REVIEWED – awaiting QA approval", "approved": "APPROVED"}[v["status"]]
        c.setFont(bold, 9)
        if v["status"] == "approved":
            c.setFillColorRGB(0.09, 0.5, 0.25)
        else:
            c.setFillColorRGB(0.75, 0.1, 0.1)
        c.drawRightString(W - m, H - m - 4, status)
        c.setFillColorRGB(0.3, 0.3, 0.3)
        c.setFont(reg, 7)
        c.drawRightString(W - m, H - m - 16, f"Printed {today.isoformat()} · page {page_no} of {len(pages)} · {v['count']} due dates · SHA-256 {v['hash'][:16]}…")
        c.setFillColorRGB(0, 0, 0)
        y = H - m - 30
        c.setFillColorRGB(0.93, 0.92, 0.95)
        c.rect(m, y - row_h, W - 2 * m, row_h, stroke=0, fill=1)
        c.setFillColorRGB(0, 0, 0)
        c.setFont(bold, 7.5)
        c.drawString(m + 2, y - row_h + 2 * mm, "Asset · checklist · interval")
        for k, mo in enumerate(MONTHS):
            c.drawCentredString(m + name_w + (k + 0.5) * col_w, y - row_h + 2 * mm, mo)
        return y - row_h

    for pi, chunk in enumerate(pages, start=1):
        y = head(pi)
        for r in chunk:
            c.setStrokeColorRGB(0.85, 0.84, 0.88)
            c.setLineWidth(0.4)
            c.line(m, y - row_h, W - m, y - row_h)
            label = f"{r['asset_code']}  {r['asset_name_en'] or ''}"
            c.setFont(bold, 7)
            c.drawString(m + 2, y - 2.6 * mm, _fit(c, label, bold, 7, name_w - 4))
            sub = " · ".join(x for x in (r.get("template_code"), f"{r['interval_value']} {UNIT.get(r['interval_unit'] or '', '')}"
                                         if r.get("interval_value") else None, r.get("vendor_name")) if x)
            c.setFont(reg, 6)
            c.setFillColorRGB(0.35, 0.35, 0.35)
            c.drawString(m + 2, y - 5.2 * mm, _fit(c, sub, reg, 6, name_w - 4))
            by_month: dict[int, list[dict]] = {}
            for e in r["entries"]:
                by_month.setdefault(e["due_date"].month, []).append(e)
            for mo, es in by_month.items():
                x = m + name_w + (mo - 1) * col_w
                text = " ".join(str(e["due_date"].day) + ("✓" if e["status"] == "completed" else "") for e in es)
                late = any(e.get("overdue") for e in es)
                done = all(e["status"] == "completed" for e in es)
                if done:
                    c.setFillColorRGB(0.89, 0.95, 0.91)
                elif late:
                    c.setFillColorRGB(0.99, 0.91, 0.9)
                elif any(e["status"] == "planned" for e in es):
                    c.setFillColorRGB(1, 1, 1)
                else:
                    c.setFillColorRGB(0.95, 0.92, 0.97)
                c.rect(x + 0.6, y - row_h + 0.6, col_w - 1.2, row_h - 1.2, stroke=0, fill=1)
                c.setFillColorRGB(0, 0, 0)
                c.setFont(reg, 6.8)
                c.drawCentredString(x + col_w / 2, y - row_h + 2.1 * mm, _fit(c, text, reg, 6.8, col_w - 2))
            c.setFillColorRGB(0, 0, 0)
            y -= row_h
        if pi == len(pages):
            p = v.get("plan") or {}
            y0 = m + 24 * mm
            c.setFont(reg, 6.5)
            c.setFillColorRGB(0.35, 0.35, 0.35)
            c.drawString(m, y0 + 4 * mm, "Day of the month shown in each cell. ✓ done · shaded green done · red overdue · white planned (from the schedule).")
            c.setFillColorRGB(0, 0, 0)
            for k, (lbl, sig) in enumerate((("Reviewed by (engineering)", p.get("reviewed_signature")),
                                             ("Approved by (QA)", p.get("approved_signature")))):
                x = m + k * (W - 2 * m) / 2
                c.setFont(bold, 8)
                c.drawString(x, y0 - 2 * mm, lbl)
                c.setFont(reg, 7.5)
                if sig:
                    c.drawString(x, y0 - 7 * mm, f"{sig['user_name']} <{sig['user_email']}>")
                    c.drawString(x, y0 - 11 * mm, f"Signed electronically {sig['signed_at'].strftime('%Y-%m-%d %H:%M UTC')}")
                    c.setFont(reg, 6)
                    c.drawString(x, y0 - 15 * mm, _fit(c, f"SHA-256 {sig['record_hash']}", reg, 6, (W - 2 * m) / 2 - 8))
                else:
                    c.line(x, y0 - 10 * mm, x + 70 * mm, y0 - 10 * mm)
        c.showPage()
    c.setTitle(title)
    c.save()
    return buf.getvalue()
