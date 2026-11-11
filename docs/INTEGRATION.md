# Connecting ComMach and Complanning

ComMach and Complanning are separate products: separate repositories, Railway services, databases and addresses
(`commach.comp11labs.com`, `complanning.comp11labs.com`). Each is validated on its own. A customer who buys
both links them once; nothing is shared at database level.

## How the link works

1. **Issue a key.** In ComMach, the company administrator opens **Company settings → Connected apps** and creates a key named
   "Complanning". The key is shown once.
2. **Enter the key in Complanning** (its connection settings arrive with Phase 6), together with the ComMach address.
3. **Match machines.** Complanning machines are matched to ComMach assets by code. A ComMach asset can also carry the
   Complanning machine code in **External reference**; the demo plant fills it for every production machine.

## API (read-only, JSON)

Every call sends `Authorization: Bearer <key>`. The key identifies the company; it cannot reach any other company.

| Endpoint | Returns |
| --- | --- |
| `GET /api/integration/v1/assets` | Every asset: `code`, names, `site_code`, `class_code`, `category`, `status`, `available` (in service or restricted), `external_ref`, `parent_id`, `updated_at` |
| `GET /api/integration/v1/events?after=<id>&limit=<n>` | Changes since the last event id the caller has seen: `asset.created`, `asset.status_changed` (payload has the code, status, previous status, availability and external reference). Use `last_id` for the next call. |

Events are written by the database in the same transaction as the change, and they are append-only. A
caller that polls `events` every few minutes never misses a change.

## Released in Phase 6

### ComMach → Complanning (in `events`)

| Event | When | Payload |
| --- | --- | --- |
| `calibration.planned`, `maintenance.planned` | A task is created, or its due date, tolerance window or status changes | `number`, `kind`, `asset_code`, `external_ref`, `site_id`, `status`, `due_date`, `window_start`, `window_end`, `duration_minutes` (from the PM checklist), `performer`, `due_by_hours` |
| `calibration.cancelled`, `maintenance.cancelled` | A task is cancelled | as above |
| `calibration.completed`, `pm.completed` | A task is completed (unchanged since Phase 2) | as above plus `performed_date`, `verdict` |
| `asset.out_of_service` | An available asset goes out of service or into quarantine (unplanned downtime) | `code`, `external_ref`, `status`, `previous_status`, `since` |
| `asset.back_in_service` | It becomes available again | as above |

Complanning books the window `window_start … window_end` (or the due date when the window opens early) as planned
downtime of the machine whose code is `external_ref` (or `asset_code`).

### Complanning → ComMach

`POST` calls carry the same bearer key; they need no other header.

**Released production plan** — `POST /api/integration/v1/production-plan`

```json
{"start": "2026-11-01T00:00:00Z", "end": "2027-01-01T00:00:00Z",
 "machines": [{"code": "TP-01", "campaigns": [{"start": "2026-11-03T06:00:00Z", "end": "2026-11-07T18:00:00Z",
                                                "label": "Paracetamol 500 mg", "ref": "CP-26-411"}]}]}
```

The plan replaces every campaign ComMach holds for those machines between `start` and `end`. `code` is the ComMach
asset code or its external reference. The answer lists machines it did not recognise. ComMach then shows, for each
open PM on a planned machine, whether its due date falls in a campaign and the free slot nearest to it inside the
tolerance window (task page, and **Results analysis → Maintenance**). Moving the date still needs an approved
reschedule.

**Run-hours** — `POST /api/integration/v1/meter-readings`

```json
{"readings": [{"code": "TP-01", "reading": 18342.5, "read_at": "2026-11-10T22:00:00Z", "unit": "hours"}]}
```

Readings must be newer and not lower than the last one for the machine (others are skipped and listed). A PM or
calibration schedule with a run-hours interval falls due when the hours since it was last done reach the interval,
or at its date, whichever comes first; the open task is then brought forward to today.

## Still to come

- Optional single sign-on across Comp11 Labs products.
