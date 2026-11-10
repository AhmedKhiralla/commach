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

## What comes in Phase 6

- **ComMach → Complanning**
  - `maintenance.planned` and `calibration.planned` events with start and end times, which Complanning imports as machine downtime.
  - `asset.out_of_service`, which becomes an unplanned downtime.
- **Complanning → ComMach**
  - The released production plan per machine, so ComMach can suggest PM dates in the gaps between campaigns.
- Optional single sign-on across Comp11 Labs products.
