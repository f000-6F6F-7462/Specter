# NATS events reference

Everything that *happens* in Specter is published on NATS. This page is the reference for the
application server: which subjects exist, what each message contains, and how to consume them
reliably. For a guided walkthrough with code, use the
[App integration guide](app-integration-guide.md).

- **Server:** `nats://127.0.0.1:4222` (monitoring on `http://127.0.0.1:8222`). No authentication:
  NATS listens on localhost only, so the application server must run on the same device.
- **Messages are JSON** (UTF-8). Timestamps are RFC 3339 in UTC, for example
  `2026-09-20T12:00:00Z`.
- **Machine-readable contract:** [`contracts/jsonschema/`](../../contracts/jsonschema/) holds a JSON
  Schema per message. It is generated from the Python models (`make contracts`), so it cannot drift
  from what Specter really sends.

## Subjects

Every subject starts with `specter.owners.{owner_id}`, so one owner's events can be followed with
`specter.owners.{owner_id}.>`.

| Subject | Message | Stored in | Published by |
|---|---|---|---|
| `specter.owners.{owner}.cameras.{camera}.match_confirmed` | [MatchConfirmed](#matchconfirmed) | `EVENTS` | camera process |
| `specter.owners.{owner}.cameras.{camera}.rule_triggered` | [RuleTriggered](#ruletriggered) | `EVENTS` | camera process |
| `specter.owners.{owner}.enrollment.status_changed` | [EnrollmentStatusChanged](#enrollmentstatuschanged) | `EVENTS` | detector |
| `specter.owners.{owner}.cameras.{camera}.status_changed` | [CameraStatusChanged](#camerastatuschanged) | `CAMERA_STATUS` | camera manager |
| `specter.owners.{owner}.configuration.changed` | [ConfigurationChanged](#configurationchanged) | `CONFIGURATION` | api |

Useful wildcards (`*` is one token, `>` is everything after):

| You want | Subject filter |
|---|---|
| Every alert of one owner | `specter.owners.{owner}.cameras.*.match_confirmed` and `...rule_triggered` |
| Every alert of every owner | `specter.owners.*.cameras.*.match_confirmed` and `...rule_triggered` |
| Everything from one camera | `specter.owners.{owner}.cameras.{camera}.>` |
| Everything of one owner | `specter.owners.{owner}.>` |

> Camera ids that Specter generates are safe subject tokens (lowercase letters, digits and
> underscores, for example `camera_4f9c…`). **Owner ids are chosen by you**, and one containing `.`,
> `*`, `>` or whitespace cannot be used in a subject. See
> [owners](app-integration-guide.md#owners-and-isolation).

### Not for the application

These exist, but belong to Specter. Do not subscribe to, publish on, or create consumers for them:

| Name | Why |
|---|---|
| `specter.enrollment.jobs` / stream `ENROLLMENT_JOBS` | A work queue. Consuming from it would steal jobs from the detector and leave photos unenrolled. |
| `specter.detector.object_detection`, `specter.detector.identification` | Private request/reply between camera processes and the detector. |
| KV bucket `match_cooldowns` | Internal alert de-duplication. |
| Durable consumers `match_alert_recorders`, `rule_alert_recorders`, `enrollment_workers` | Used by Specter. Give your own consumers different names. |

## Envelope

Every message starts with the same four fields:

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | string | Currently `"1.0"`. The **major** number changes only when a change breaks existing consumers. |
| `message_id` | string | Unique id, `message_` + 32 hex characters. Also sent as the `Nats-Msg-Id` header. For `match_confirmed` and `rule_triggered` it is **also the alert's id** in the HTTP API. |
| `occurred_at` | timestamp | When Specter created the message. |
| `owner_id` | string | Which owner the message belongs to. |

Rules for reading messages safely:

- **Ignore fields you do not know.** A minor version may add fields. Specter itself rejects unknown
  fields when *it* reads a message, but a consumer should not.
- **Check the major version** of `schema_version` and log or skip a message whose major you do not
  support, instead of guessing.
- **Bounding boxes are fractions** of the frame (0 to 1, from the top-left), as
  `{ "x", "y", "width", "height" }`. Multiply by the image size you display.

## Messages

### MatchConfirmed

A tracked person was confirmed as a watchlist target. Subject `…cameras.{camera}.match_confirmed`.

```json
{
  "schema_version": "1.0",
  "message_id": "message_9f1c…",
  "occurred_at": "2026-09-20T12:00:00Z",
  "owner_id": "acme",
  "camera_id": "camera_a1…",
  "track_id": 17,
  "watchlist_id": "watchlist_w1…",
  "target_id": "target_t1…",
  "modality": "face",
  "similarity_ratio": 0.62,
  "margin_ratio": 0.11,
  "threshold_ratio": 0.45,
  "object_class": "person",
  "bounding_box": { "x": 0.41, "y": 0.22, "width": 0.12, "height": 0.38 },
  "first_seen_at": "2026-09-20T11:59:52Z",
  "frame_captured_at": "2026-09-20T12:00:00Z",
  "snapshot_path": "evidence/acme/2026/09/20/message_9f1c….jpg"
}
```

| Field | Notes |
|---|---|
| `modality` | `face` or `appearance`: which kind of embedding produced the match. |
| `similarity_ratio` | How close the person is to the target, 0 to 1. |
| `margin_ratio` | How far the best target is ahead of the runner-up, 0 to 1. |
| `threshold_ratio` | The watchlist threshold that was applied. |
| `track_id` | Stable for one person while they stay on one camera. Not unique across cameras or restarts. |
| `snapshot_path` | **Internal path on the device. Do not use it.** Fetch the image with `GET /owners/{owner}/alerts/{message_id}/snapshot`. `null` if the image could not be saved. |

### RuleTriggered

A rule fired for a tracked object. Subject `…cameras.{camera}.rule_triggered`. Which optional
fields are set depends on `rule_kind`.

```json
{
  "schema_version": "1.0",
  "message_id": "message_7b2e…",
  "occurred_at": "2026-09-20T12:00:00Z",
  "owner_id": "acme",
  "camera_id": "camera_a1…",
  "rule_id": "rule_r1…",
  "rule_kind": "zone_occupancy",
  "zone_id": "zone_z1…",
  "track_id": 4,
  "object_class": "person",
  "bounding_box": { "x": 0.41, "y": 0.22, "width": 0.12, "height": 0.38 },
  "dwell_seconds": 12.5,
  "crossing_direction": null,
  "frame_captured_at": "2026-09-20T12:00:00Z",
  "snapshot_path": null
}
```

| `rule_kind` | Set | Null |
|---|---|---|
| `zone_occupancy` | `zone_id`, `dwell_seconds` | `crossing_direction` |
| `line_crossing` | `crossing_direction` (`left_to_right` or `right_to_left`) | `zone_id`, `dwell_seconds` |

### EnrollmentStatusChanged

One embedding of one reference image was enrolled or rejected. Subject
`specter.owners.{owner}.enrollment.status_changed` (an **owner** event, not a camera event).

```json
{
  "schema_version": "1.0",
  "message_id": "message_2…",
  "occurred_at": "2026-09-20T12:00:00Z",
  "owner_id": "acme",
  "target_id": "target_t1…",
  "reference_image_id": "image_i1…",
  "modality": "face",
  "status": "rejected",
  "rejection_reason": "too_blurry",
  "quality_score_ratio": 0.21
}
```

| Field | Values |
|---|---|
| `status` | `embedded` (usable) or `rejected`. |
| `rejection_reason` | Set when rejected: `no_face_detected`, `multiple_faces`, `low_detection_score`, `too_small`, `too_blurry`, `extreme_pose`, `too_dark`, `too_bright`, `no_person_detected`, `multiple_people`, `unreadable_image`, `processing_failed`. |
| `quality_score_ratio` | 0 to 1 when the image was measured, otherwise `null`. |

A photo produces one message **per modality** (`face`, `appearance`), so expect several events per
photo. A target's overall state is `enrollment_status` on the API (`queued`, `partial`, `ready`,
`failed`): after any of these events, read the target to get it rather than computing it yourself.

### CameraStatusChanged

A camera's live status changed. Subject `…cameras.{camera}.status_changed`, stored in
`CAMERA_STATUS`, which keeps **only the latest message per camera**.

```json
{
  "schema_version": "1.0",
  "message_id": "message_1…",
  "occurred_at": "2026-09-20T12:00:00Z",
  "owner_id": "acme",
  "camera_id": "camera_a1…",
  "status": "running"
}
```

`status` is one of `starting`, `running`, `reconnecting` (the stream dropped, retrying), `stopped`,
`failed`. The message is published when the status *changes*, not periodically.

### ConfigurationChanged

An entity was created, updated or deleted through the API. Subject
`specter.owners.{owner}.configuration.changed`, stored in `CONFIGURATION`, which keeps **only the
latest message per owner**.

```json
{
  "schema_version": "1.0",
  "message_id": "message_3…",
  "occurred_at": "2026-09-20T12:00:00Z",
  "owner_id": "acme",
  "entity_kind": "camera",
  "entity_id": "camera_a1…",
  "change_kind": "updated"
}
```

`entity_kind`: `camera`, `zone`, `rule`, `watchlist`, `target`. `change_kind`: `created`, `updated`,
`deleted`.

This is a **hint, not a log**: only the latest change per owner is kept, and it says what changed,
not the new values. It is mainly for Specter's own processes. Your application made the change
itself, so you rarely need it, except to keep several application instances or open UI sessions in
sync (then re-read the entity from the API).

## Streams

| Stream | Subjects | Retention | Use it for |
|---|---|---|---|
| `EVENTS` | `match_confirmed`, `rule_triggered`, `enrollment.status_changed` | 7 days **or** 256 MiB, whichever comes first, on disk | Alerts and enrollment results, including catching up after downtime. |
| `CAMERA_STATUS` | `status_changed` | Latest message per camera, on disk | The current status of every camera when you start. |
| `CONFIGURATION` | `configuration.changed` | Latest message per owner, on disk | Rarely needed. |
| `ENROLLMENT_JOBS` | (internal) | Work queue | Never. |

After 7 days an event is gone from NATS, but **alerts stay in the database** and are always
available through `GET /owners/{owner}/alerts/...`. Treat NATS as the live feed and the API as the
history.

## Camera health (key-value bucket)

Bucket **`camera_health`**: one entry per running camera process, refreshed every 3 seconds and
**expiring after 10 seconds**, kept in memory.

- **Key:** `{owner_id}.{camera_id}`
- **Value:**

```json
{ "owner_id": "acme", "camera_id": "camera_a1…", "status": "running", "reported_at": "2026-09-20T12:00:00Z" }
```

- **A missing key means no process is reporting** (stopped, crashed, or still starting), which is
  itself useful information.
- Watch one owner's cameras with the key filter `{owner_id}.>`. **A watcher is not told when an
  entry expires**: it sees a `PUT` every ~3 seconds while the camera is alive, and then simply
  nothing. To detect a dead camera, keep the time of the last `PUT` per key and treat more than
  ~10 seconds of silence as offline, or read the key (`get` returns nothing once it has expired).
- `GET /owners/{owner}/cameras/{id}` returns this as `live_status` (`null` when the key is missing),
  so a plain request/response UI does not need to read the bucket at all.

`status_changed` events tell you *when a camera changed*; the bucket tells you *whether it is alive
right now*. Use events for the UI, and the bucket or `live_status` for the current state.

## Consuming reliably

JetStream stores messages, so a consumer that was offline can catch up. There are three ways to
read, from weakest to strongest guarantee:

| Way | Sees | Use when |
|---|---|---|
| Core subscription (`nc.subscribe`) | Only messages published while connected | Never for alerts: anything sent while your server restarts is lost. |
| Ephemeral / ordered JetStream consumer | History, then live | Reading a snapshot of state, for example the current `CAMERA_STATUS` of all cameras at startup. |
| **Durable JetStream consumer** with explicit acks | Everything after its last acknowledged message, across restarts | **Alerts and enrollment results.** |

For alerts, create one durable consumer on `EVENTS` with your own name, for example
`app_alerts`, acknowledge each message **after** you have stored or forwarded it, and:

- **De-duplicate on `message_id`.** Delivery is at-least-once: an unacknowledged message is
  delivered again. `message_id` is unique and stable.
- **Do not acknowledge before you have handled the message**, or a crash loses the alert.
- **Do not let one bad message block the rest.** Log it and acknowledge (or `term`) it.
- Choose where a *new* consumer starts: only new messages (`new`), or up to 7 days of history
  (`all` / by start time). It is fixed when the consumer is created.
- Several application instances can share one durable consumer name to split the work, or use
  different names to each receive every message.

Publishing side, for reference: Specter publishes with JetStream and a `Nats-Msg-Id` header equal to
`message_id`, so a retried publish is stored once within the server's duplicate window.

## Compatibility

- Adding an optional field, or a new message type, is a **minor** change: your consumer keeps
  working if it ignores what it does not know.
- Renaming or removing a field, or changing its meaning, raises the **major** `schema_version`.
- Enum values (`status`, `rejection_reason`, …) can grow. Handle an unknown value with a sensible
  default rather than crashing.
