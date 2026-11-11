# ComMach roadmap

The certificate AI was moved forward into Phase 2, so the Al-Andalous Medical Company demo can show it now.

| Phase | Content | Status |
| --- | --- | --- |
| 1 | **Foundation and asset register.** Hierarchy, classes, vendors, assets and components, signed status changes, files, nameplate AI, AI register import, QR labels, overview, demo plant, connected-apps API | Released |
| 2 | **Calibration** with certificate AI and OOT impact assessments (see below) | Released |
| 3 | **Preventive maintenance** and annual plan approval (see below) | Released |
| 4 | **Tasks and notifications** (see below) | Released |
| 5 | **Forms and records** (see below) | Released |
| 6 | **Analysis, assistant and Complanning** (see below) | Released |

## Phase 2 – Calibration

- **Calibration templates** per class or model: points, nominal values, tolerances and units, plus the reference standards required.
- **Schedules.** Calibration **floats**: the next due date is counted from the actual calibration date.
  - Presets (monthly … every N years), custom intervals and offsets, and bulk apply with a preview of the next 12 due dates.
  - **Tolerance windows** set in days, weeks, months or % of the interval, with **asymmetric** limits (for example −any / +0), set by frequency band and overridable per schedule.
- **External vendors.** A calibration can be assigned to a vendor plus an internal responsible person. The flow is: request → out for calibration → returned → certificate.
- **Readings.** As-found / as-left readings with an automatic **out-of-tolerance verdict**. A reference standard is blocked if it is itself overdue.
- **Certificate scan → results (AI).**
  - The AI extracts: certificate number, lab and accreditation, dates, environmental conditions, standards used, the readings table, uncertainty, and the statement of conformity.
  - ComMach checks:
    - serial number
    - date window
    - required points
    - tolerances
    - uncertainty ratio
    - the lab's accreditation validity
  - Side-by-side review against the certificate image, then an e-signature. The audit trail keeps the AI value and the final value for every field.
- **OOT handling.** Automatic quarantine, plus an **impact assessment draft** (AI) listing affected rooms, products and the period since the last good calibration.
- **Labels and dates.** Labels print the calibrated-on and due dates; the overview shows the nearest due date and overdue counts.

Released in Phase 2 as described, plus: task acceptance (switchable), four-eyes review of a completed calibration,
signed send-to-vendor and cancellation, schedule changes carried into the open task with a reason for change,
and certificate readings becoming the points when a schedule has no template.
Still to come with later phases: e-mail and calendar reminders and escalation (Phase 4), the results-analysis
dashboard (Phase 6).

## Phase 3 – Preventive maintenance

- **PM templates and checklists.** Item types: √/X with remarks, reading with limits, text and photo.
- **Manual PDF → checklist + intervals + spare parts (AI)**, with page references. The draft is approved and versioned.
- **Schedules.** PM schedules are **fixed** (calendar-based) on the working calendar (Friday, holidays, Ramadan), with staggering and usage-based triggers ready for Companorama run hours.
- **Plan approval.** The annual calibration plan and annual PM plan are signed by a supervisor and QA before tasks are released.
- **Execution on the shop floor.** Phone/tablet execution: accept the task, then work the checklist, add photos, record spare parts and man-hours, and sign Done by / Checked by.

Released in Phase 3 as described, plus: a printable annual plan (PDF grid with the signatures), four-eyes on plans
(the reviewer cannot approve), plans that must be reviewed again when they change after the review, several checklists
per machine, and a working calendar (weekend days, closures) that can also apply to calibration.
Still to come: usage-based triggers from Companorama run hours (Phase 6, with the Complanning link).

## Phase 4 – Tasks and notifications

- **To-Do list.** Each user gets a To-Do list and a **calendar feed (ICS)** for Google Calendar and Outlook.
- **Email.** Sent through Gmail (comp11labs@gmail.com, SMTP over SSL, as in the naming worker; Railway Pro allows outbound SMTP).
  - One daily digest per user.
  - Immediate emails only for escalations.
- **Escalation** (configurable per activity type and criticality):
  - an unaccepted task is escalated
  - on the due date
  - when the tolerance window ends
  - level 2 → department head and QA
  - level 3 → site head, with a deviation record
- **Rescheduling with approval.**
  - Within tolerance: the supervisor approves.
  - Beyond tolerance: supervisor and QA approve.
- **Handover and delegation.**
  - Handover of a deactivated user's tasks, schedules, approvals and escalation roles.
  - Temporary delegation for leave.

Released in Phase 4 as described, plus: an in-app notification centre, an e-mail log, a "Run escalations now"
button, one escalation e-mail per person per run (not one per task), and delegation-aware assignment everywhere
(My tasks, the calendar feed, acceptance and the "My work" list).

## Phase 5 – Forms and records

- **Block designer.** Logo, title, format number, revision, header fields, data block, table and columns, footer, and Prepared / Approved / Authorized signatures.
- **Word templates with merge fields.** For example the customer's own QA027/F01 annual calibration schedule, EN002 annual PM schedule, EN003 breakdown note and history card, EN004 building checklist, and QA027/L01 status label.
- **Approval.** Templates are approved and versioned as controlled documents.

Released in Phase 5 as described for plans, calibration and PM records, the history card (EN003/F02) and the status
label, plus: a live page preview in the designer, a starter Word file per record type, a check that blocks approval
of a Word file with unknown merge fields, and a numbered controlled-copy log with the SHA-256 of every print.
The EN003 breakdown note and the EN004 building checklist wait for breakdown work orders and building checks, which are
not in this version; their layouts can already be drafted on the history card and PM record forms.

## Phase 6 – Analysis, assistant and Complanning

- **Results dashboard:**
  - drift charts with tolerance bands and the projected crossing date
  - OOT rate
  - first-time pass rate
  - vendor performance
  - interval recommendations
- **"Ask ComMach" assistant** (as in Complanning 0.9). It answers from the register and records with each user's own access.
- **Complanning connection.** Planned calibration and PM windows and breakdowns become machine downtime, and PM is suggested in gaps between campaigns.
- **Run-hours triggers.** PM (or calibration) due after N run-hours or cycles, or at its date, whichever comes first.

Released in Phase 6 as described, plus: drift charts on every instrument, vendor turnaround and record-closing times,
inbound run-hours from Complanning, the free-slot suggestion on each PM task with a one-click reschedule request, and
a log of every assistant question with the look-ups it made. The assistant reads; it never changes a record.

Pilot readiness (after Phase 6): validation pack with the executed OQ report, system check, load test — see
[validation/README.md](validation/README.md).

Possible next steps: breakdown work orders (EN003 breakdown note), building checklists (EN004), single sign-on across
Comp11 Labs products, and spare-parts stock.
