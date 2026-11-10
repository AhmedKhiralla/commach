# ComMach — 21 CFR Part 11 traceability

This maps each Part 11 requirement to how ComMach meets it, and to the automated test that proves it where one
exists. It supports, but does not replace, the customer's own validation (URS, risk assessment, IQ/OQ/PQ),
procedures and training. ComMach is configurable, off-the-shelf software (GAMP 5 category 4). This document
covers Phase 1 (asset register) and is extended with each phase.

## Subpart B — Electronic records

| Clause | Requirement | How ComMach meets it | Evidence |
|---|---|---|---|
| 11.10(a) | Validation; ability to discern invalid or altered records | Every file is stored content-addressed by SHA-256 and re-hashed on every download; a mismatch refuses the file. The audit trail is hash-chained per company and can be verified on demand (Audit → Verify integrity). Every signature carries the SHA-256 of what was signed. AI suggestions keep the SHA-256 of the raw answer. | `test_audit_trail_hash_chain`, `test_files_and_nameplate_ai` |
| 11.10(b) | Accurate and complete copies, human-readable and electronic | Asset register export to Excel; audit trail export to Excel/CSV, stamped with exporter and time; files downloadable unchanged; printed labels. Exports and label printing are audited. | `test_template_export_and_labels`, `test_audit_export_logs_event` |
| 11.10(c) | Protection of records for their retention period | Signatures, status history, files and integration events are append-only (database triggers reject UPDATE and DELETE; the application role has SELECT and INSERT only). Files removed from an asset stay in the record with the reason. Users and vendor contacts are deactivated, never deleted. Retention and backup of the database and volume follow the customer's procedure (Railway backups or equivalent). | `test_audit_trail_is_append_only`, `test_vendor_with_contacts` |
| 11.10(d) | Limiting system access to authorised individuals | Named accounts created by the company administrator; roles per site; PostgreSQL row-level security isolates companies even if application code is wrong; sessions time out on inactivity. | `test_tenant_isolation`, `test_rls_enforced_in_database`, `test_other_company_cannot_see_assets` |
| 11.10(e) | Secure, computer-generated, time-stamped audit trail | Database triggers write the audit trail — the application cannot skip it. Each entry records who, what, when (server UTC), the old and new values, the **reason for change** given by the user, and links to the previous entry's hash. Users cannot change or delete it. Sign-ins, signatures and failed signatures, file uploads and downloads, imports, exports, label printing, AI suggestions and their review are recorded too. | `test_hierarchy_rules` (reason stored), `test_audit_records_old_and_new_values`, `test_audit_trail_hash_chain` |
| 11.10(f) | Operational system checks (enforced sequencing) | A new asset starts as draft; its status changes **only** through a signed status change — the database refuses any other UPDATE of the status. Allowed transitions are enforced (e.g. a disposed asset cannot return to service). Locations follow building > area > room; an asset cannot become a component of its own component. AI suggestions can be reviewed once and their content never changes. | `test_status_needs_signature_and_role`, `test_hierarchy_rules`, `test_asset_create_and_components` |
| 11.10(g) | Authority checks | Every endpoint checks the user's role at the site. Signing checks the role for the change being signed: QA approver or engineering manager approve into service; only QA releases from quarantine; a maintenance technician may take a machine out of service but not return it. The administrator flag never implies a signing authority. | `test_status_needs_signature_and_role` |
| 11.10(h) | Device checks (validity of the source of data input) | Uploads are identified by their content (magic bytes), not their name; unsupported types are refused. Import previews validate every row (codes, classes, sites, ranges, parents) before anything is saved. | `test_files_and_nameplate_ai`, `test_import_customer_sheet_with_ai` |
| 11.10(i) | Training of people who develop, maintain or use the system | Customer procedure. Comp11 Labs provides the user guide, the administrator guide and this document. | — |
| 11.10(j) | Written policies holding individuals accountable for e-signatures | Customer procedure. The signing dialog shows the legal statement that the electronic signature is the binding equivalent of a handwritten one. | — |
| 11.10(k) | Controls over system documentation | Documentation is versioned with the source in Git; each release has a version number shown in the app. | — |
| 11.30 | Open systems | Hosted over HTTPS only (secure cookies, HSTS, strict content security policy). AI providers receive only the photo or the sheet headers and sample rows being mapped, over HTTPS, and only when the company switches the AI function on. | — |

## AI as a data-entry aid

Every AI answer is stored as a **suggestion**: provider, model, prompt version, SHA-256 of the input and of the
raw answer. It becomes data only when a person saves the record. At that point ComMach records who accepted it, when, the final
values, and **every field the person corrected** (AI value → final value). The record shows "proposed by
&lt;model&gt; for &lt;user&gt;, accepted by &lt;user&gt;". The person who saves remains responsible for the content.

### Calibration records (Phase 2)

- A completed calibration, an approved template and an approved impact assessment can no longer be changed:
  database triggers refuse updates to them and to their readings and standards. The only later change is the one-time review signature.
- A task can be completed only with a signature (`calibration_completed`). The hash covers the record fields,
  every reading, the standards used and the verdict.
- The verdict (pass / adjusted / fail) is computed from the limits, not typed. A failed calibration changes the asset
  to quarantined under the same signature.
- Review is by a second person (`calibration_reviewed`); the person who completed the calibration cannot review it.
- Schedule changes need a reason for change, which is stored with the audit entry.
- Certificate AI and impact-assessment AI follow the AI rules below: the AI value and the final value of every field
  are recorded when the person signs or saves.

Evidence: `tests/test_calibration.py`.

## Subpart C — Electronic signatures

| Clause | Requirement | How ComMach meets it | Evidence |
|---|---|---|---|
| 11.50(a) | Signed records show name, date and time, and meaning | Each signature stores the signer's full name and email, server UTC time, the meaning (in service, release, quarantined, out of service …), the exact statement shown, the reason, and the hash of the record signed. The asset's status history shows all of these. | `test_status_needs_signature_and_role` |
| 11.50(b) | Same controls as records; shown in human-readable form | Signatures are append-only rows covered by the audit trail and shown on the asset page. | `test_audit_trail_is_append_only` |
| 11.70 | Signature/record linking | Each signature carries the SHA-256 of the asset's registration data, the new status, reason and note. One signing act over many assets writes one signature per asset, each with its own hash. | `test_status_needs_signature_and_role` |
| 11.100(a) | Unique to one individual, not reused or reassigned | Email is unique; accounts are deactivated, never deleted or renamed to someone else. | foundation tests |
| 11.100(b) | Identity verified before assigning | Customer procedure; the company administrator creates the account. | — |
| 11.100(c) | Certification to the agency | Customer letter to FDA (and local equivalents such as EDA). | — |
| 11.200(a)(1) | Two distinct components (ID and password); re-entered for each signing | Each signature asks for email and password again, even within a session. | `test_status_needs_signature_and_role` |
| 11.200(a)(2) | Used only by the genuine owner | The signer must be the signed-in user; failed attempts are audited (SIGNATURE_FAILED) and count toward lockout, which also ends the sessions. | `test_status_needs_signature_and_role` |
| 11.200(b) | Biometrics | Not used. | — |
| 11.300(a)–(e) | ID/password controls | Unique email; scrypt hashing; password history; expiry and forced change; lockout after failed sign-ins or signatures; administrator reset revokes sessions. | foundation tests |

## Demonstration data

Demo assets loaded with **Load the demo plant** receive their statuses directly. Their status history marks each
entry as **demo data** (source `demo_data`), never as signed, and the load is recorded as a `SAMPLE_DATA` event.
Demo data belongs in a demonstration company, not in a validated production company.

## What the customer validates

- **IQ:** the deployed version, environment variables, database and volume, backups.
- **OQ:** run the automated test suite against the deployed version and execute the scripted test cases in the
  validation pack. These cover sign-in, roles, signatures and transitions, the audit trail and its verification, imports, labels and exports.
- **PQ:** the customer's own register, roles and procedures.
