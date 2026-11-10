"""Asset labels with a QR code. Scanning the code opens the asset on a phone or tablet (after sign-in).

Two layouts: a sheet of labels on A4 (cut out or use label stock) and one label per page for label printers.
Calibration labels print the last calibration date, the next due date and who did and checked it; blank lines remain where a value is not known yet.
"""
from __future__ import annotations

import io
import os

from starlette.responses import Response

from ..api import ApiError, Call, endpoint
from ..config import settings
from ..perms import require_site

STATUS_TEXT = {
    "draft": "NOT YET RELEASED", "in_service": "IN SERVICE", "restricted": "RESTRICTED USE", "standby": "STANDBY",
    "out_of_service": "OUT OF SERVICE - DO NOT USE", "quarantined": "QUARANTINE - DO NOT USE",
    "out_for_calibration": "AT CALIBRATION - DO NOT USE", "retired": "RETIRED", "disposed": "DISPOSED",
    "transferred": "TRANSFERRED",
}
STATUS_COLOUR = {"in_service": (0.09, 0.5, 0.25), "restricted": (0.75, 0.5, 0.0), "draft": (0.35, 0.35, 0.35)}
RED = (0.75, 0.1, 0.1)


def _font() -> tuple[str, str]:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for reg, bold in (("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),):
        if os.path.exists(reg) and os.path.exists(bold):
            try:
                pdfmetrics.registerFont(TTFont("CM", reg))
                pdfmetrics.registerFont(TTFont("CM-Bold", bold))
                return "CM", "CM-Bold"
            except Exception:
                break
    return "Helvetica", "Helvetica-Bold"


def _base_url(call: Call) -> str:
    if settings.public_url:
        return settings.public_url
    h = call.request.headers
    proto = h.get("x-forwarded-proto") or call.request.url.scheme
    host = h.get("x-forwarded-host") or h.get("host") or call.request.url.netloc
    return f"{proto}://{host}"


def _fit(c, text: str, font: str, size: float, width: float) -> str:
    from reportlab.pdfbase.pdfmetrics import stringWidth
    text = text or ""
    while text and stringWidth(text, font, size) > width:
        text = text[:-2] + "…" if len(text) > 2 else ""
        if text.endswith("……"):
            text = text[:-1]
    return text


def _draw(c, x: float, y: float, w: float, h: float, a: dict, url: str, company: str, fonts: tuple[str, str]):
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing
    from reportlab.lib.units import mm
    reg, bold = fonts
    c.setLineWidth(0.6)
    c.setStrokeColorRGB(0.2, 0.2, 0.2)
    c.roundRect(x, y, w, h, 2 * mm)
    pad = 2.5 * mm
    # QR on the right
    q = min(h - 2 * pad - 4 * mm, w * 0.36)
    widget = QrCodeWidget(url, barLevel="M")
    b = widget.getBounds()
    d = Drawing(q, q, transform=[q / (b[2] - b[0]), 0, 0, q / (b[3] - b[1]), 0, 0])
    d.add(widget)
    renderPDF.draw(d, c, x + w - pad - q, y + h - pad - q)
    tx = x + pad
    tw = w - 3 * pad - q
    top = y + h - pad
    c.setFillColorRGB(0.35, 0.35, 0.35)
    c.setFont(reg, 6)
    c.drawString(tx, top - 6, _fit(c, company.upper(), reg, 6, tw))
    c.setFillColorRGB(0, 0, 0)
    c.setFont(bold, 11)
    c.drawString(tx, top - 18, _fit(c, a["code"], bold, 11, tw))
    c.setFont(reg, 7.5)
    c.drawString(tx, top - 28, _fit(c, a["name_en"], reg, 7.5, tw))
    c.setFillColorRGB(0.3, 0.3, 0.3)
    c.setFont(reg, 6.5)
    loc = " / ".join(p for p in (a["site_code"], a["location_code"]) if p)
    c.drawString(tx, top - 37, _fit(c, f"{a['class_name_en']} · {loc}" if loc else a["class_name_en"], reg, 6.5, tw))
    if a.get("serial_no"):
        c.drawString(tx, top - 45, _fit(c, f"S/N {a['serial_no']}", reg, 6.5, tw))
    # status band
    band_h = 5.5 * mm
    col = STATUS_COLOUR.get(a["status"], RED)
    c.setFillColorRGB(*col)
    c.rect(x + pad, y + pad, w - 2 * pad, band_h, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont(bold, 7.5)
    c.drawCentredString(x + w / 2, y + pad + 1.8 * mm, STATUS_TEXT.get(a["status"], a["status"].upper()))
    # calibration dates (or blank lines to fill by hand)
    if h >= 45 * mm and a.get("calibration_required"):
        c.setFillColorRGB(0.2, 0.2, 0.2)
        c.setFont(reg, 6.5)
        fmt = (lambda d: d.strftime("%d %b %Y") if d else "")
        lines = (("Calibrated on:", fmt(a.get("cal_last"))), ("Calibration due:", fmt(a.get("cal_due"))),
                 ("Done by:", a.get("cal_by") or ""), ("Checked by:", a.get("cal_checked") or ""))
        yy = y + pad + band_h + 3 * mm
        for i, (t, val) in enumerate(reversed(lines)):
            ly = yy + i * 4.2 * mm
            c.setFont(reg, 6.5)
            c.drawString(tx, ly, t)
            end = x + w - 3 * pad - q if ly > y + h - pad - q else x + w - pad
            if val:
                c.setFont(bold, 7)
                c.drawString(tx + 22 * mm, ly, _fit(c, val, bold, 7, end - tx - 22 * mm))
            else:
                c.setLineWidth(0.3)
                c.line(tx + 22 * mm, ly - 0.6, end, ly - 0.6)


@endpoint(auth="tenant_user")
def labels_pdf(call: Call):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    ids = [x for x in (call.query.get("ids") or "").split(",") if x][:400]
    if not ids:
        raise ApiError(422, "invalid", "Choose the assets to label.")
    size = call.query.get("size", "large")
    layout = call.query.get("layout", "a4")
    rows = call.tx.q(
        "SELECT a.id, a.site_id, a.code, a.name_en, a.serial_no, a.status, a.calibration_required,"
        " c.name_en AS class_name_en, s.code AS site_code, l.code AS location_code,"
        " (SELECT t.performed_date FROM tasks t WHERE t.asset_id = a.id AND t.kind = 'calibration' AND t.status = 'completed'"
        "   ORDER BY t.performed_date DESC LIMIT 1) AS cal_last,"
        " (SELECT t.completed_by_name FROM tasks t WHERE t.asset_id = a.id AND t.kind = 'calibration' AND t.status = 'completed'"
        "   ORDER BY t.performed_date DESC LIMIT 1) AS cal_by,"
        " (SELECT t.reviewed_by_name FROM tasks t WHERE t.asset_id = a.id AND t.kind = 'calibration' AND t.status = 'completed'"
        "   ORDER BY t.performed_date DESC LIMIT 1) AS cal_checked,"
        " (SELECT min(t.due_date) FROM tasks t WHERE t.asset_id = a.id AND t.kind = 'calibration'"
        "   AND t.status NOT IN ('completed', 'cancelled')) AS cal_due"
        " FROM assets a JOIN asset_classes c ON c.id = a.class_id JOIN sites s ON s.id = a.site_id"
        " LEFT JOIN locations l ON l.id = a.location_id WHERE a.id = ANY(%s::uuid[]) ORDER BY a.code", ids)
    for r in rows:
        require_site(call, str(r["site_id"]))
    company = call.tx.val("SELECT name FROM tenants WHERE id = %s", call.tenant_id) or ""
    base = _base_url(call)
    w, h = (90 * mm, 55 * mm) if size == "large" else (60 * mm, 32 * mm)
    fonts = _font()
    buf = io.BytesIO()
    if layout == "roll":
        c = canvas.Canvas(buf, pagesize=(w + 4 * mm, h + 4 * mm))
        for r in rows:
            _draw(c, 2 * mm, 2 * mm, w, h, r, f"{base}/a/{r['code']}", company, fonts)
            c.showPage()
    else:
        c = canvas.Canvas(buf, pagesize=A4)
        pw, ph = A4
        margin, gap = 10 * mm, 4 * mm
        cols = max(1, int((pw - 2 * margin + gap) // (w + gap)))
        rws = max(1, int((ph - 2 * margin + gap) // (h + gap)))
        for i, r in enumerate(rows):
            k = i % (cols * rws)
            if i and k == 0:
                c.showPage()
            cx = margin + (k % cols) * (w + gap)
            cy = ph - margin - (k // cols + 1) * h - (k // cols) * gap
            _draw(c, cx, cy, w, h, r, f"{base}/a/{r['code']}", company, fonts)
        c.showPage()
    c.setTitle("ComMach asset labels")
    c.save()
    call.tx.event("LABELS_PRINTED", "assets", None, f"{len(rows)} labels",
                  {"codes": [r["code"] for r in rows][:400], "size": size, "layout": layout})
    return Response(buf.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="commach-labels.pdf"'})
