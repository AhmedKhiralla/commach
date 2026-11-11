# ComMach

Calibration and preventive maintenance for pharmaceutical and other GMP plants, by **Comp11 Labs**.
A line extension of Complanning, Comproof and Companorama: same look, same security model, its own
address (`commach.comp11labs.com`) and its own database, with an API that connects it to Complanning.

## Released so far

- **Phase 1 – Foundation and asset register**
  - Companies (tenants), sites and **site groups**, users with roles per site, and company settings:
    - feature switches
    - terminology
    - password and session policy
    - AI provider and daily limit
  - Roles:
    - company administrator
    - QA approver
    - engineering manager
    - engineering supervisor
    - QA calibration officer
    - maintenance technician
    - requester
    - viewer / auditor
  - **Hierarchy:** Group → Site → Building → Area → Room. Every level is optional, and production lines sit alongside it.
  - **Asset register:**
    - equipment and machines, instruments, gauges, sensors and transmitters, tools, reference standards,
      utilities, facility and safety items
    - classes the company can edit
    - **parent/child assets** (a machine and its sensors and gauges)
    - portable assets with a home and a current location
    - GMP criticality, calibration and PM flags
    - vendors with accreditation and contacts
    - departments
  - **Lifecycle status changes only with an electronic signature** (draft → in service, restricted, standby,
    out of service, quarantined, out for calibration, retired, disposed, transferred). The database refuses
    any unsigned change. One signing act can approve many assets, for example a whole imported register.
  - **Controlled files** (photos, nameplates, manuals, certificates): content-addressed, write-once, and re-checked
    against their SHA-256 on every download.
  - **AI (Qwen), always a draft that a person accepts:**
    - *Nameplate photo → asset record.* The fields the AI filled are marked; the person's corrections are recorded.
    - *Messy Excel register → clean import.* Columns, types and status words are mapped by name or by AI,
      previewed row by row, missing locations are created, and imports land as drafts for one signed approval.
  - **QR labels.** A sheet of labels on A4 or one per page for label printers; scanning opens the asset on a phone.
  - **Overview.** Counts at every level of the hierarchy, with All / Calibration / Maintenance toggles.
  - **One-click demo plant.** About 140 assets with components, two calibration labs and realistic statuses.
  - **Connected apps.** A per-company key and a read-only API (`/api/integration/v1/assets`, `/events`) for Complanning.

- **Phase 2 – Calibration**
  - **Templates** per instrument type: points, nominal values, tolerances (± value, ± % or limits), method and
    standards required. Approved by e-signature; changed only by a new version, which moves the schedules with it.
  - **Schedules**, one asset or hundreds at once, with a preview of the next due dates:
    - interval in days, weeks, months or years; **floating** (from the date done) or fixed
    - **tolerance windows** per interval band in company settings, asymmetric, in days, weeks, months or % of the
      interval, overridable per schedule
    - internal, or an **external vendor** with an internal responsible person
    - assigned to roles and/or named people; certificate required or not
  - **Calibration work list:** overdue, due soon, at vendor, in progress, to review, out of tolerance, with KPIs.
  - **Calibration task:** accept, send to the vendor (signed; the asset goes *out for calibration*), as-found / as-left
    readings with live in/out marks, reference standards (blocked when overdue), certificate upload,
    **complete and sign**, four-eyes **review and sign**, signed cancellation. The next task is created from the actual date.
  - **Certificate AI.** A PDF or photo of the certificate is read: certificate number, date, environment, readings and
    uncertainty, mapped to the points, and checked (serial, date, coverage, tolerance, uncertainty ratio, lab
    conformity statement, accreditation, standards). The person applies, corrects and signs; the AI value and the final
    value of every field are recorded.
  - **Out of tolerance:** a failed calibration quarantines the asset under the completion signature; any OOT opens an
    **impact assessment** (period since the last good calibration, scope, products, risk, actions, conclusion) with an
    **AI first draft**, approved by QA with an e-signature.
  - Overview, asset list and labels show the next due date and overdue counts; the asset page has a Calibration tab.
  - The demo plant now carries about two years of calibration history, two OOT results and their assessments.

- **Phase 3 – Preventive maintenance and annual plans**
  - **PM checklists**: check (OK / not OK / N/A with remarks), reading with limits, text / value and photo items, each
    with the page of the manual; spare parts to replace, suggested interval and estimated time. Approved by
    e-signature (engineering manager or QA) and versioned.
  - **Manual PDF → checklists (AI).** The AI reads the manual and proposes one checklist per frequency, every item and
    spare part with its page number. The engineer edits and creates the drafts; the AI draft and the final checklist are
    compared and recorded; each draft is then approved by signature.
  - **Schedules** are fixed (calendar-based) by default, several per machine, planned for one asset or many at once.
    A **working calendar** (weekend days, holidays, shutdowns, Ramadan if wanted) moves due dates to working days.
  - **Annual plans** per site for calibration and for PM: a year grid of done, open and projected due dates, reviewed
    by an engineering supervisor and approved by QA with e-signatures (two different people), printable as a PDF.
    With *plan approval* on (the default), work is released only from an approved plan.
  - **Execution on phones and tablets:** accept the job, work the checklist with large buttons, take photos, record
    readings, spare parts used and man-hours, sign **Done by**; a supervisor signs **Checked by**. Findings are
    recorded with the job without quarantining the machine.
  - The demo plant now also has 10 PM checklists, about 40 machines, utilities and safety items with a year of PM history,
    approved plans for this year and a next-year maintenance plan waiting for QA approval.

- **Phase 4 – Tasks and notifications**
  - **My tasks**: overdue, due this week and later, waiting for acceptance, to review and sign, approvals waiting for
    you (reschedules, annual plans, templates and checklists, impact assessments), and open deviations.
  - **Notifications** under the bell for every escalation, reschedule decision, delegation and handover.
  - **E-mail** through Gmail (comp11labs@gmail.com, SMTP over SSL): one daily summary per person at the company's hour,
    and one immediate e-mail per scheduler run for escalations. Each person can switch either off. An e-mail log is in
    Company settings.
  - **Calendar feed** (ICS) for Google Calendar and Outlook: a private address per person; only its hash is stored.
  - **Escalation**: level 1 when a task is not accepted N days before it is due and on the due date (assignees and
    supervisors); level 2 when the tolerance window ends (plus the department head and QA); level 3 N days later by
    GMP criticality (plus the site head) with a **deviation** record that QA closes with root cause, actions and an
    e-signature.
  - **Rescheduling with approval**: inside the tolerance window the supervisor signs; beyond it the supervisor and
    then QA sign (different people, never the requester).
  - **Delegation** for leave (the delegate sees and works the tasks; signing still needs their own role) and
    **handover** of a leaving user's tasks, schedules, head roles and delegations, optionally with their site roles.

- **Phase 5 – Forms and records**
  - **Forms and records** holds the company's controlled formats for the annual calibration and PM plans, calibration
    and PM records, the equipment history card and the status label.
  - **Block designer** with a live preview: page size and orientation, logo, title, subtitle, annexure / format number
    and revision, field grids, tables (plan rows with scheduled / executed lines, readings, standards, spare parts,
    history), text, Prepared / Approved / Authorized signature boxes and a footer with "Page x of y".
  - **Word templates**: upload the company's own .docx with merge fields such as `{{asset.code}}`; a table row with
    `{{se_rows.m1}}` (or results, standards, parts, history) repeats once per item. A starter Word file with all the
    fields can be downloaded; unknown fields are flagged and block approval.
  - Forms are **controlled documents**: drafted, approved by QA with an e-signature, frozen, versioned (the default
    moves to the new version) and retired with a reason.
  - Every print is a **controlled copy**: numbered CC-YYYY-NNNNN, stamped with the form version, logged with its
    SHA-256 and the person who printed it. A draft form prints with "DRAFT – NOT FOR GMP USE".
  - **Print** buttons on calibration and PM tasks, the annual plans, and each asset (history card, status label).
  - The demo company has the six standard forms approved and in use (QA027-00/F01, F02, L01, EN002-01/F01, F02, EN003-F02).

- **Phase 6 – Analysis, assistant and Complanning**
  - **Results analysis** for calibration and maintenance: completed, on-time, out-of-tolerance (or findings) and
    first-time pass rates, overdue now, a month-by-month chart, results by asset class and vendor performance
    (on time, OOT, turnaround, days to close the record, missing certificates).
  - **Drift charts** on each instrument's calibration tab: as-found history per point inside its tolerance band, the
    drift line since the last adjustment and the projected date it crosses a limit.
  - **Interval recommendations**: shorten after an out-of-tolerance result or when drift reaches a limit before the
    next due date; extend after three passes with at most half the tolerance used. Applied with a reason like any
    schedule change.
  - **Run-hours triggers**: meter readings per machine (entered, or sent by Complanning); a schedule can also fall
    due after N run-hours or cycles, whichever comes first. The open task is brought forward automatically.
  - **Complanning connection**: planned calibration and PM windows and unplanned downtime go out as events; the
    released production plan comes in, and each PM shows the free slot between campaigns nearest its due date.
  - **Ask ComMach**: questions in English or Arabic, answered by the AI only from read-only look-ups ComMach runs
    with the user's own access; answers link to the records, and every question is logged.

- **Pilot readiness**
  - **Validation pack** (`docs/validation`): user requirements traced to automated OQ test cases, an IQ checklist for
    Railway, suggested PQ scenarios, and `python -m validation.run_oq`, which runs the suite on a throw-away database
    and prints the executed OQ report (PDF) for QA signature.
  - **System check** for the platform owner after each deployment: database, migrations, row-level security,
    audit-trail integrity, stored files against their SHA-256, e-mail and AI sign-in, scheduler and settings.
  - Load-tested with 1,022 assets and 25 simultaneous users (see `docs/validation`).

The web app is English and Arabic (right-to-left), with light, dark and system themes, and works on phones and tablets.

See [docs/ROADMAP.md](docs/ROADMAP.md) for the next phases (all six phases are released),
[docs/PART11.md](docs/PART11.md) for the 21 CFR Part 11 traceability and [docs/INTEGRATION.md](docs/INTEGRATION.md)
for connecting Complanning.

## How it is built

| Part | Technology |
| --- | --- |
| Web app | React 19 + TypeScript, bundled with esbuild (`frontend/`) |
| API | Python 3.13, Starlette, pydantic, raw SQL through psycopg 3 (`backend/app/`) |
| Database | PostgreSQL 16, UTF-8 (`backend/migrations/*.sql`) |
| Files | Content-addressed store on a Railway volume (`/data`) |
| AI | Any OpenAI-compatible API; Qwen (Alibaba Cloud Model Studio) by default |
| Hosting | One Docker image on Railway: the API serves the web app too |

### Security model (21 CFR Part 11 controls)

- **Tenant isolation in the database.** Every request runs as the restricted role `commach_app` with row-level
  security, so a company's queries only ever return its own rows.
- **Audit trail written by the database.**
  - Triggers record every insert, change and removal, with the user, server time, old and new values, and the
    **reason for change** the user gave.
  - The trail is append-only and **hash-chained** per company (Audit trail → Verify integrity).
- **Electronic signatures.**
  - Email and password are re-entered at every signing.
  - The signer's name, time, meaning, the statement shown and a SHA-256 of what was signed are stored with the signature.
  - Failed signatures count toward lockout.
- **Workflow guards in the database.**
  - An asset's status cannot change without a signature.
  - Signatures, status history, files and integration events are append-only.
  - AI suggestions are kept exactly as received and reviewed once.
- **Accounts.** Unique users, scrypt hashing, password policy and history, lockout, inactivity timeout, and
  one-time passwords that must be changed.

## Deploy on Railway

ComMach is a **separate service with its own database**, so it is validated, backed up and sold on its own.

1. **Add the code.** In your Railway project, click **+ New → GitHub Repo** and pick `AhmedKhiralla/ComMach`.
   Railway finds `railway.json` and builds the `Dockerfile`.
2. **Add a database.** Click **+ New → Database → PostgreSQL**. Use a new one, not Complanning's.
3. **Add a volume** to the ComMach service, mounted at `/data`. Uploaded files live there; without a volume,
   uploads are refused rather than lost at the next deploy.
4. **Set the variables** on the ComMach service:

   | Variable | Value |
   | --- | --- |
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (reference to the new database) |
   | `PLATFORM_ADMIN_EMAIL` | your email, used for the first platform administrator |
   | `PLATFORM_ADMIN_PASSWORD` | a temporary password (10+ characters, upper, lower, digit, symbol); you change it at first sign-in |
   | `QWEN_API_KEY` | your Alibaba Cloud Model Studio key (switches AI on) |
   | `PUBLIC_URL` | `https://commach.comp11labs.com` (used in label QR codes, e-mail links and the calendar feed) |
   | `GMAIL_APP_PASSWORD` | the Gmail app password of comp11labs@gmail.com (same as the naming worker) — switches e-mail on |
   | optional `MAIL_FROM` / `MAIL_FROM_NAME` | default `comp11labs@gmail.com` / `ComMach by Comp11 Labs` |
   | optional `MAIL_BCC` | a copy of every e-mail, e.g. for the first weeks of a pilot |
   | optional `SCHEDULER_SECONDS` | how often escalations, digests and the e-mail outbox run (default 300; 0 = off) |
   | optional `QWEN_BASE_URL` | default `https://ap-southeast-1.maas.qwencloudapi.com/compatible-mode/v1` (QwenCloud, Singapore); for an Alibaba Cloud Model Studio key use `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` |
   | optional `QWEN_VISION_MODEL` / `QWEN_TEXT_MODEL` | default `qwen3-vl-plus` / `qwen-plus`. If the account has no model of that name, ComMach tries the known alternatives (`qwen3.8-flash`, `qwen-vl-max`, `qwen-vl-plus`, …), uses the first that works and logs it |

5. **Add the domain.**
   - Under **Settings → Networking**, add the custom domain `commach.comp11labs.com`.
   - In Netlify DNS, add the `CNAME` record Railway shows, pointing `commach` at the Railway target.
6. **Deploy.** Migrations run at start-up. Sign in as the platform administrator, change the password, then create
   the first company and its administrator. To try it quickly, sign in as that administrator and choose
   **Load the demo plant** on the Overview.

The health check is `GET /api/health` (it also reports whether file storage is persistent).

## Develop locally

```bash
# database
createdb commach
# API (http://localhost:8000)
cd backend && pip install -r requirements-dev.txt
DATABASE_URL=postgresql://postgres@localhost/commach COOKIE_SECURE=false \
  PLATFORM_ADMIN_EMAIL=you@example.com PLATFORM_ADMIN_PASSWORD='Initial#Pass2026' \
  uvicorn app.main:app --reload
# web app (rebuilds on change; the API serves frontend/dist)
cd frontend && npm install && npm run dev
```

### Tests

The tests start a real server against a fresh PostgreSQL database and a stand-in AI service:

```bash
cd backend
TEST_DATABASE_ADMIN_URL=postgresql://postgres@localhost:5432/postgres pytest -q
```

They cover:
- tenancy isolation, authentication controls, and the append-only, hash-chained audit trail
- hierarchy rules, asset components and cycle prevention
- signed status changes: role rules, failed signatures, bulk approval, and the database refusing unsigned changes
- files, nameplate AI with recorded corrections, and AI import mapping
- labels, Excel template and export, demo data, and the integration API
