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

The web app is English and Arabic (right-to-left), with light, dark and system themes, and works on phones and tablets.

See [docs/ROADMAP.md](docs/ROADMAP.md) for the next phases (calibration with certificate AI comes next),
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
   | `PUBLIC_URL` | `https://commach.comp11labs.com` (printed in the label QR codes) |
   | optional `QWEN_VISION_MODEL` / `QWEN_TEXT_MODEL` | default `qwen-vl-plus` / `qwen-plus` |
   | optional `QWEN_BASE_URL` | default `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` |

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
