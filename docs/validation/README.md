# ComMach validation pack (pilot readiness)

This folder supports the customer's computerised-system validation of ComMach (21 CFR Part 11, EU GMP Annex 11,
GAMP 5 category 4 — configured product). Comp11 Labs supplies the items marked **supplier**; the customer's QA owns
the plan, the PQ and the release.

| Document | Who | Where |
| --- | --- | --- |
| User requirements (URS) and traceability matrix | supplier | [traceability.md](traceability.md), generated from `backend/validation/catalogue.py` |
| Part 11 / Annex 11 assessment | supplier | [../PART11.md](../PART11.md) |
| Installation qualification (IQ) checklist | supplier template, customer executes | below |
| Operational qualification (OQ): automated test cases and executed report | supplier | `python -m validation.run_oq` (below) |
| Performance test results | supplier | below |
| Performance qualification (PQ) / user acceptance | customer | suggested scenarios below |
| Validation plan, risk assessment, summary report, SOPs, training records | customer | — |

## IQ — installation on Railway

Record the value or a screenshot for each line.

1. GitHub repository `AhmedKhiralla/ComMach` is private; the deployed commit hash is recorded.
2. Railway service built from that commit with the repository `Dockerfile`; region recorded.
3. Railway PostgreSQL 16 service in the same region; **daily backups on**; one backup restored to a test service and opened.
4. A Railway volume is attached for the file store (`RAILWAY_VOLUME_MOUNT_PATH`), and is backed up.
5. Environment variables set (values not recorded): `DATABASE_URL`, `PUBLIC_URL=https://commach.comp11labs.com`,
   `QWEN_API_KEY`, `GMAIL_APP_PASSWORD`; `COOKIE_SECURE` not set to false; `SCHEDULER_SECONDS` not 0.
6. The custom domain serves ComMach over HTTPS with a valid certificate.
7. The platform owner signs in, changes the start-up password, and `PLATFORM_ADMIN_PASSWORD` is removed from the service.
8. **System check** (platform owner → System check → *Deep check*) shows *Ready*: database, 8 of 8 migrations,
   row-level security, audit-trail integrity, file store, e-mail sign-in, AI models, cookies, public address,
   scheduler. Print the page to PDF and attach it.
9. In Company settings → AI, *Test connection* passes for the text and vision models.

## OQ — automated test run

The OQ runs the full automated suite on a **separate, throw-away database** (never the production database): it
creates the database, applies every migration, starts ComMach and runs each test case as real users of several
companies through the web API, plus direct database checks of the controls that must hold outside the application.

```bash
cd backend
pip install -r requirements-dev.txt
TEST_DATABASE_ADMIN_URL=postgresql://postgres:<password>@<host>:5432/postgres \
  python -m validation.run_oq --out ComMach-OQ-report.pdf --executed-by "Name, role"
```

The PDF has a cover with the result and blank signature lines, the environment (commit, SHA-256 fingerprint of the
code, Python, PostgreSQL, libraries), the traceability matrix with the result per requirement, every test case with
its objective, acceptance criterion and result, and a deviations section. The run fails if any test fails, if a
requirement has no test, or if a test is not traced to a requirement.

Run it on the commit that is deployed, ideally against a PostgreSQL service of the same version as production
(for example a temporary Railway Postgres). AI features are tested against a local stand-in with fixed answers; the
live provider is covered by IQ step 9.

After a change: re-run the OQ on the new commit; QA decides from the change and the risk whether PQ scenarios need to
be repeated.

## Performance

Measured on 11 Nov 2026 on a development machine (1 application process, PostgreSQL 16 on the same host) with one
company of **1,022 assets on 7 sites**, 1,242 schedules, 6,480 tasks and 18,000 results:

| Operation | Time |
| --- | --- |
| Task lists, asset lists, My tasks, plans, analysis summary, asset and task pages | 3–31 ms |
| Interval recommendations (all instruments) | 166 ms |
| Annual plan PDF / on the company form | 55 ms / 281 ms |
| Register export (Excel) | 242 ms |
| Scheduler run (escalations, run-hours, digests) for the company | 208 ms |
| Audit-trail export (≈ 11,500 entries) | 942 ms |
| 25 users at once, 375 mixed requests | 83 requests/s, median 224 ms, 95th percentile 379 ms, no errors |

Al-Andalous has hundreds of assets, so this gives a margin of several times. On Railway, keep the app and the database
in the same region.

## PQ — suggested scenarios for the pilot (customer)

Run with Al-Andalous master data and real users, with QA observing; record evidence in the customer's PQ protocol.

1. Import the equipment register from the customer's Excel file; QA checks a sample of 20 assets against the source.
2. Set tolerance bands and the working calendar to the calibration and maintenance SOPs.
3. Approve one calibration template per instrument type and the PM checklists for one production line.
4. Prepare, review and approve the annual calibration plan (QA027-00/F01) and the annual PM plan (EN002-01/F01);
   print both on the company forms and compare with the paper formats.
5. Execute three internal calibrations on a tablet, one with an out-of-tolerance result through the impact assessment.
6. Send one instrument to an external vendor, read its certificate with the AI, check and complete it.
7. Execute two PMs on the shop floor with photos and spare parts, one with a finding.
8. Let one task go overdue in a test company and follow the escalation to a deviation and its closure.
9. Request a reschedule inside and outside the tolerance window.
10. Hand over the work of a leaving user; delegate during leave.
11. Review the audit trail for all of the above; export it; run the integrity check.
12. Ask ComMach three questions and check the answers against the records.
