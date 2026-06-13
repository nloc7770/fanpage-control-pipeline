# API Contracts

Base URL: `http://localhost:8080`. All responses are JSON unless otherwise
noted. Timestamps are ISO-8601 UTC.

The canonical source of truth for these shapes is
`packages/shared-py/shared_py/schemas.py` (Python) and
`packages/shared-types/src/index.ts` (TypeScript).

## REST endpoints

### `POST /jobs`

Create a new job.

```
Content-Type: application/json

{
  "source_url": "https://...",
  "options": {
    "enable_diarization": true,        // optional, overrides ENABLE_DIARIZATION
    "target_clip_count": 8,            // optional, 1..20
    "language_hint": "en"              // optional, ISO 639-1
  }
}
```

Response `201 Created`:

```
{
  "id": "uuid",
  "source_url": "...",
  "status": "queued",
  "progress_pct": 0,
  "current_stage": null,
  "error_message": null,
  "source_metadata": null,
  "created_at": "...",
  "updated_at": "...",
  "finished_at": null
}
```

Errors: `400` (bad URL), `409` (already queued for same URL within 60s),
`429` (rate limit), `503` (broker unreachable).

### `GET /jobs`

List jobs.

Query params:
- `limit` (default 20, max 100)
- `offset` (default 0)
- `status` (one of the `JobStatus` values, repeatable)

Response `200`:

```
{ "jobs": [JobDTO, ...], "total": 42 }
```

### `GET /jobs/{id}`

Job detail.

Response `200`: a `JobDTO`. `404` if missing.

### `GET /jobs/{id}/clips`

List the clips for a job.

Response `200`:

```
{ "clips": [ClipDTO, ...] }
```

`ClipDTO.edit_plan` is the full `EditPlan` once Qwen has produced it,
otherwise `null`.

### `GET /jobs/{id}/events`  (SSE)

Long-lived `text/event-stream`. On connect, the server replays recent
events from the `logs` table, then streams new events.

Each frame:

```
event: <type>
id: <ulid|timestamp>
data: <json payload>
```

Event types: see `SSEEventType` in `shared_py/events.py`:

- `job.created` -- `{ source_url }`
- `job.progress` -- `{ stage, pct, message? }`
- `job.stage_changed` -- `{ from, to }`
- `job.failed` -- `{ stage, error }`
- `job.completed` -- `{ clip_count, duration_s }`
- `clip.planned` -- `{ clip_id, clip_index, title, virality_score }`
- `clip.rendering` -- `{ clip_id, clip_index, pct }`
- `clip.rendered` -- `{ clip_id, clip_index, asset_id }`
- `clip.failed` -- `{ clip_id, clip_index, error }`
- `worker.heartbeat` -- `{ worker_id, worker_type, task }`

The server emits a keep-alive `: ping` line every 15s.

### `GET /assets/{id}/download`

Streams the underlying file with the recorded MIME type and a
`Content-Disposition: attachment; filename="..."` header. `404` if the
asset row does not exist or the file is gone from storage.

### `GET /healthz`

Returns `200 { "status": "ok", "db": "up", "redis": "up" }` once both
dependencies respond, `503` otherwise.

## Errors

All non-2xx responses use the same shape:

```
{ "error": { "code": "string", "message": "string", "details": { ... } } }
```

Common codes: `validation_error`, `not_found`, `conflict`, `rate_limited`,
`upstream_unavailable`, `internal_error`.
