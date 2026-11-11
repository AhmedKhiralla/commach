"""Run the ComMach automated OQ and print the executed report (PDF), with the requirement traceability matrix.

    TEST_DATABASE_ADMIN_URL=postgresql://postgres:...@host:5432/postgres \\
    python -m validation.run_oq --out OQ-report.pdf --executed-by "Name, role"

The suite creates its own throw-away database on that server, applies every migration, starts ComMach and runs each
test through the web API and directly against the database. Nothing touches a production database.

    python -m validation.run_oq --matrix-md ../docs/validation/traceability.md   # the matrix only, no test run
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import platform
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
from validation.catalogue import REQUIREMENTS, TESTS  # noqa: E402

DOC_NO = "CM-OQ-001"


# ------------------------------------------------------------------ facts about the build and the environment
def _run(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, cwd=BACKEND, capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def source_fingerprint() -> str:
    """SHA-256 over the application code, migrations and tests: identifies exactly what was tested."""
    h = hashlib.sha256()
    for p in sorted([*BACKEND.glob("app/**/*.py"), *BACKEND.glob("migrations/*.sql"), *BACKEND.glob("tests/*.py"),
                     *BACKEND.glob("validation/*.py")]):
        h.update(str(p.relative_to(BACKEND)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def environment() -> list[tuple[str, str]]:
    commit = os.environ.get("RAILWAY_GIT_COMMIT_SHA") or _run("git", "rev-parse", "HEAD") or "unknown"
    dirty = _run("git", "status", "--porcelain", "--", ".")
    pg = ""
    try:
        import psycopg
        with psycopg.connect(os.environ["TEST_DATABASE_ADMIN_URL"], connect_timeout=10) as c:
            pg = c.execute("SHOW server_version").fetchone()[0]
    except Exception as e:  # reported, not fatal
        pg = f"not reachable ({type(e).__name__})"
    pkgs = []
    for name in ("starlette", "uvicorn", "pydantic", "psycopg", "reportlab", "python-docx", "openpyxl", "pypdfium2"):
        try:
            from importlib.metadata import version
            pkgs.append(f"{name} {version(name)}")
        except Exception:
            pass
    return [
        ("System", "ComMach — calibration and preventive maintenance"),
        ("Source commit", commit + (" (with local changes)" if dirty else "")),
        ("Source fingerprint (SHA-256)", source_fingerprint()),
        ("Python", sys.version.split()[0]),
        ("PostgreSQL server", pg),
        ("Operating system", f"{platform.system()} {platform.release()} ({platform.machine()})"),
        ("Host", platform.node()),
        ("Libraries", ", ".join(pkgs)),
    ]


# ------------------------------------------------------------------ run the suite
def run_suite(extra: list[str]) -> tuple[dict[str, dict], str, float]:
    with tempfile.TemporaryDirectory() as tmp:
        xml_path = Path(tmp) / "junit.xml"
        start = dt.datetime.now(dt.timezone.utc)
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml_path}", "tests", *extra],
                              cwd=BACKEND, capture_output=True, text=True)
        took = (dt.datetime.now(dt.timezone.utc) - start).total_seconds()
        results: dict[str, dict] = {}
        if xml_path.exists():
            for case in ET.parse(xml_path).getroot().iter("testcase"):
                name = case.get("name", "")
                outcome, message = "passed", ""
                for tag in ("failure", "error"):
                    el = case.find(tag)
                    if el is not None:
                        outcome, message = "failed", (el.get("message") or el.text or "")[:1500]
                if case.find("skipped") is not None:
                    outcome, message = "skipped", case.find("skipped").get("message", "")
                results[name] = {"module": case.get("classname", "").split(".")[-1], "outcome": outcome,
                                 "seconds": float(case.get("time") or 0), "message": message}
        return results, proc.stdout[-4000:] + proc.stderr[-2000:], took


# ------------------------------------------------------------------ traceability
def test_ids() -> dict[str, str]:
    return {name: f"OQ-{i:03d}" for i, name in enumerate(TESTS, start=1)}


def coverage(results: dict[str, dict] | None) -> dict[str, list[str]]:
    mapped = {t for r in REQUIREMENTS for t in r[4]}
    out = {"requirements_without_tests": [r[0] for r in REQUIREMENTS if not r[4]],
           "tests_without_requirement": sorted(set(TESTS) - mapped),
           "catalogue_tests_missing": sorted(t for r in REQUIREMENTS for t in r[4] if t not in TESTS)}
    if results is not None:
        out["tests_not_in_catalogue"] = sorted(set(results) - set(TESTS))
        out["catalogue_tests_not_run"] = sorted(set(TESTS) - set(results))
    return out


def req_outcome(tests: list[str], results: dict[str, dict]) -> str:
    outs = [results.get(t, {}).get("outcome", "not run") for t in tests]
    if not outs:
        return "not covered"
    if all(o == "passed" for o in outs):
        return "passed"
    return "failed" if "failed" in outs else "incomplete"


def matrix_markdown() -> str:
    ids = test_ids()
    lines = ["# ComMach — user requirements and traceability", "",
             "Generated from `backend/validation/catalogue.py`. Each requirement is verified by the automated OQ test",
             "cases listed; `python -m validation.run_oq` executes them and prints the signed-off-ready OQ report.", "",
             "| Requirement | Area | Description | Part 11 / Annex 11 | OQ test cases |", "|---|---|---|---|---|"]
    for rid, area, text, ref, tests in REQUIREMENTS:
        lines.append(f"| {rid} | {area} | {text} | {ref or '—'} | {', '.join(f'{ids[t]} `{t}`' for t in tests)} |")
    lines += ["", "## OQ test cases", "", "| ID | Test | Objective | Acceptance criterion |", "|---|---|---|---|"]
    for name, (obj, acc) in TESTS.items():
        lines.append(f"| {ids[name]} | `{name}` | {obj} | {acc} |")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ the PDF
def build_pdf(path: Path, results: dict[str, dict], env: list[tuple[str, str]], executed_by: str, took: float, log: str) -> str:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9, leading=12)
    small = ParagraphStyle("s", parent=body, fontSize=7.5, leading=9.5)
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=15, spaceAfter=6, textColor=colors.HexColor("#3b0a52"))
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11.5, spaceBefore=10, spaceAfter=4, textColor=colors.HexColor("#3b0a52"))
    title = ParagraphStyle("t", parent=h1, fontSize=22, leading=27, alignment=TA_CENTER)
    sub = ParagraphStyle("st", parent=body, fontSize=11, leading=15, alignment=TA_CENTER)
    P = lambda t, st=body: Paragraph(str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), st)
    ids = test_ids()
    n = len(results)
    passed = sum(1 for r in results.values() if r["outcome"] == "passed")
    failed = [k for k, r in results.items() if r["outcome"] == "failed"]
    cov = coverage(results)
    gaps = any(cov[k] for k in cov)
    verdict = "PASSED" if n and not failed and not gaps else "FAILED"
    now = dt.datetime.now(dt.timezone.utc)
    grid = TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b9a7c6")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                       ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3eaf8")), ("FONTSIZE", (0, 0), (-1, -1), 8),
                       ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)])
    tone = {"passed": colors.HexColor("#137a4c"), "failed": colors.HexColor("#b42318")}

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawString(15 * mm, 10 * mm, f"{DOC_NO} · ComMach OQ executed report · {now:%Y-%m-%d %H:%M} UTC")
        canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    st: list = [Spacer(1, 40 * mm), P("ComMach", title), P("Operational Qualification (OQ)", sub),
                P("Executed automated test report and requirement traceability", sub), Spacer(1, 12 * mm)]
    cover = [["Document", DOC_NO], ["Executed", f"{now:%d %b %Y %H:%M} UTC"], ["Executed by", executed_by or "—"],
             ["Test cases", f"{n} run · {passed} passed · {len(failed)} failed"], ["Requirements", str(len(REQUIREMENTS))],
             ["Duration", f"{took:.0f} s"], ["Overall result", verdict]]
    t = Table([[P(a), P(b)] for a, b in cover], colWidths=[50 * mm, 100 * mm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b9a7c6")), ("FONTSIZE", (0, 0), (-1, -1), 9),
                           ("TEXTCOLOR", (1, 6), (1, 6), tone["passed" if verdict == "PASSED" else "failed"])]))
    st += [t, Spacer(1, 18 * mm), P("Approval", h2)]
    sig = Table([["Role", "Name", "Signature", "Date"], ["Executed by", "", "", ""], ["Reviewed by (QA)", "", "", ""],
                 ["Approved by", "", "", ""]], colWidths=[40 * mm, 50 * mm, 50 * mm, 30 * mm], rowHeights=[None, 14 * mm, 14 * mm, 14 * mm])
    sig.setStyle(grid)
    st += [sig, PageBreak()]

    st += [P("1. Purpose and scope", h1),
           P("This report records the execution of the automated Operational Qualification of ComMach. It shows, for each "
             "user requirement, the test cases that verify it and their result, so the customer's QA can review and approve "
             "the system for GMP use together with its own PQ (installation at the site, master data, user acceptance)."),
           P("Method: the suite creates a new, empty PostgreSQL database, applies every migration, starts the ComMach server "
             "and runs each test case through the web API as real users of several companies, plus direct database checks "
             "of the controls that must hold even outside the application (row-level security, append-only records). AI "
             "functions are tested against a local stand-in that returns fixed answers, so the results do not depend on "
             "an outside model; the live AI provider is checked separately by the connection test in Company settings."),
           P("2. Environment", h2)]
    t = Table([[P(a, small), P(b, small)] for a, b in env], colWidths=[45 * mm, 135 * mm])
    t.setStyle(grid)
    st += [t]
    st += [P("3. Summary", h2)]
    rows = [["Requirements", str(len(REQUIREMENTS))], ["Test cases in catalogue", str(len(TESTS))], ["Test cases run", str(n)],
            ["Passed", str(passed)], ["Failed", str(len(failed))],
            ["Requirements without a test", ", ".join(cov["requirements_without_tests"]) or "none"],
            ["Tests without a requirement", ", ".join(cov["tests_without_requirement"] + cov.get("tests_not_in_catalogue", [])) or "none"],
            ["Catalogue tests not run", ", ".join(cov.get("catalogue_tests_not_run", []) + cov["catalogue_tests_missing"]) or "none"],
            ["Overall result", verdict]]
    t = Table([[P(a), P(b)] for a, b in rows], colWidths=[60 * mm, 120 * mm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b9a7c6")), ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    st += [t, PageBreak()]

    st += [P("4. Traceability matrix", h1)]
    data = [[P(x, small) for x in ("Requirement", "Description", "Part 11 / Annex 11", "Test cases", "Result")]]
    for rid, area, text, ref, tests in REQUIREMENTS:
        out = req_outcome(tests, results)
        data.append([P(f"{rid}\n{area}", small), P(text, small), P(ref or "—", small),
                     P(", ".join(ids.get(x, x) for x in tests), small), P(out.upper(), small)])
    t = Table(data, colWidths=[24 * mm, 82 * mm, 26 * mm, 30 * mm, 18 * mm], repeatRows=1)
    t.setStyle(grid)
    for i, (_, _, _, _, tests) in enumerate(REQUIREMENTS, start=1):
        out = req_outcome(tests, results)
        t.setStyle(TableStyle([("TEXTCOLOR", (4, i), (4, i), tone.get(out, colors.HexColor("#9a5b00")))]))
    st += [t, PageBreak()]

    st += [P("5. Test cases executed", h1)]
    data = [[P(x, small) for x in ("ID", "Test case", "Objective", "Acceptance criterion", "Result")]]
    order = list(TESTS) + [k for k in results if k not in TESTS]
    for name in order:
        r = results.get(name)
        obj, acc = TESTS.get(name, ("(not in the catalogue)", "—"))
        res = (r["outcome"].upper() + f"\n{r['seconds']:.1f} s") if r else "NOT RUN"
        data.append([P(ids.get(name, "—"), small), P(f"{name}\n({r['module'] if r else '—'})", small), P(obj, small), P(acc, small), P(res, small)])
    t = Table(data, colWidths=[14 * mm, 42 * mm, 58 * mm, 48 * mm, 18 * mm], repeatRows=1)
    t.setStyle(grid)
    for i, name in enumerate(order, start=1):
        o = (results.get(name) or {}).get("outcome", "failed")
        t.setStyle(TableStyle([("TEXTCOLOR", (4, i), (4, i), tone.get(o, colors.HexColor("#9a5b00")))]))
    st += [t]

    st += [P("6. Deviations", h2)]
    if not failed and not gaps:
        st += [P("None. Every test case passed and every requirement is covered.")]
    else:
        for name in failed:
            st += [KeepTogether([P(f"{ids.get(name, '—')} {name}: FAILED", body), P(results[name]["message"], small)])]
        for k, v in cov.items():
            if v:
                st += [P(f"{k.replace('_', ' ').capitalize()}: {', '.join(v)}")]
        st += [P("Each deviation must be investigated, corrected and the OQ repeated, or justified and approved by QA.")]
    st += [P("7. Conclusion", h2),
           P("The automated OQ " + ("met all acceptance criteria." if verdict == "PASSED" else "did not meet all acceptance criteria; see Deviations.")
             + " Review and approval by QA above completes this document.")]
    if failed:
        st += [PageBreak(), P("Appendix: test run output (tail)", h2), P(log, small)]

    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=18 * mm,
                            title=f"{DOC_NO} ComMach OQ executed report", author=executed_by or "ComMach")
    doc.build(st, onFirstPage=on_page, onLaterPages=on_page)
    return verdict


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="ComMach-OQ-report.pdf")
    ap.add_argument("--executed-by", default="")
    ap.add_argument("--matrix-md", help="write the traceability matrix as Markdown and stop")
    ap.add_argument("pytest_args", nargs="*")
    a = ap.parse_args()
    if a.matrix_md:
        Path(a.matrix_md).write_text(matrix_markdown(), encoding="utf-8")
        cov = coverage(None)
        print(f"Wrote {a.matrix_md}: {len(REQUIREMENTS)} requirements, {len(TESTS)} test cases.", {k: v for k, v in cov.items() if v} or "")
        return 0
    if not os.environ.get("TEST_DATABASE_ADMIN_URL"):
        print("Set TEST_DATABASE_ADMIN_URL to a PostgreSQL server where a throw-away test database can be created.", file=sys.stderr)
        return 2
    results, log, took = run_suite(a.pytest_args)
    verdict = build_pdf(Path(a.out), results, environment(), a.executed_by, took, log)
    print(f"{verdict}: {sum(r['outcome'] == 'passed' for r in results.values())}/{len(results)} test cases passed. Report: {a.out}")
    return 0 if verdict == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
