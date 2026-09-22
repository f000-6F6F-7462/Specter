# App integration guide

This guide is for the developer of the **application server** that sits in front of Specter: the
code that owns users, shows the UI, and sends people the alerts. The examples are TypeScript
(Node 22), but nothing here depends on it: Specter speaks plain HTTP and NATS.

You will do two things:

1. **Ask and change** through the [HTTP API](http-api.md): cameras, zones, rules, watchlists,
   targets, reviewing alerts, live video.
2. **Listen** to [NATS](nats-events.md): alerts, camera status, enrollment results.

Read [the architecture page](../architecture.md) first if you have not: it is short, and the
diagrams there explain every flow used below.

## Contents

- [What you connect to](#what-you-connect-to)
- [Owners and isolation](#owners-and-isolation)
- [Run Specter locally](#run-specter-locally)
- [Quick start](#quick-start)
- [Generate types](#generate-types)
- [Recipe: receive alerts](#recipe-receive-alerts)
- [Recipe: follow camera status](#recipe-follow-camera-status)
- [Recipe: add a camera and start it](#recipe-add-a-camera-and-start-it)
- [Recipe: enroll targets with photos](#recipe-enroll-targets-with-photos)
- [Recipe: show and review alerts](#recipe-show-and-review-alerts)
- [Recipe: live video](#recipe-live-video)
- [Going to production checklist](#going-to-production-checklist)
- [Pitfalls](#pitfalls)

## What you connect to

| What | Where | Auth |
|---|---|---|
| HTTP API | `http://127.0.0.1:8000` | `Authorization: Bearer <token>` |
| NATS + JetStream | `nats://127.0.0.1:4222` | none |
| The token | `make api-token` (in Docker: `/etc/specter/api.token` in the `api` container); from source: `.dev/secrets/api.token` | created by Specter on first start |
| Live WebRTC media (optional) | port `8555`, TCP and UDP, on the device | none |

Everything listens on `127.0.0.1`, so **your application server must run on the same device**
(Specter's own design: nothing is exposed except WebRTC media). NATS has no authentication, and
anyone who can reach it can read every alert and write anything, so do not publish port 4222 or 8000
on a network without adding your own protection first.

Two rules follow from this:

- The **API token is a service credential.** Load it on the server, never ship it to a browser or
  app.
- Specter has **no users**. Your users log in to *your* application; you decide what each may see,
  then call Specter on their behalf.

## Owners and isolation

Every entity in Specter belongs to an **owner**, and almost every API path starts with
`/owners/{owner_id}`. An owner is Specter's unit of separation: cameras, watchlists, targets,
alerts and events of one owner are invisible to another (an id from another owner gives `404`).

**You choose the owner id.** There is no "create owner" call: an owner exists once something is
created under it. Typical mappings:

| Your product | Owner id |
|---|---|
| One company, one device | a constant, such as `acme` |
| Several customers on one device | one owner per customer: `customer_123` |
| Every user has private cameras | one owner per user: `user_456` |

Rules for the id:

- It ends up inside NATS subjects (`specter.owners.{owner_id}.…`) and file paths. **Use lowercase
  letters, digits and underscores** (like Specter's own ids). The API does not check this for you.
  An owner id containing `.`, `*`, `>` or whitespace cannot be used in a NATS subject, so alerts for
  it cannot be published; `/` and `..` are refused in file paths.
- Keep it stable. It is the key to all of that owner's data.
- `DELETE /owners/{owner_id}` deletes **everything** of an owner, including alerts and evidence.
  Use it for "close this account".

**Isolation is your job.** The token allows any owner. Before every call, decide which owner the
logged-in user maps to and put that in the path; never take the owner id from user input. When you
receive a NATS event, its `owner_id` tells you whose it is, and only your application knows which
users may see it.

## Run Specter locally

Specter's processes run in Docker, next to NATS, Qdrant and go2rtc. From the repository root:

```bash
make models     # once: exports and downloads the AI models (takes a while)
make up         # builds the image and starts everything; safe to run again
```

That starts the API, the camera manager and the detector, and restarts any of them if it crashes.
Camera processes need no command: the camera manager starts one for every camera you start through
the API. Useful next to it:

| Command | What it does |
|---|---|
| `make status` | State of every container. |
| `make logs` | Follow every container's log. |
| `make api-token` | Print the API token. |
| `make down` | Stop everything. Data is kept. |

Check that it is up:

```bash
curl -s http://127.0.0.1:8000/health
# {"status":"ok","version":"…","is_nats_connected":true}
```

- Interactive API docs: <http://127.0.0.1:8000/docs>
- The API is published on `127.0.0.1:8000` and NATS on `127.0.0.1:4222`. If your application server
  also runs in Docker, add it to the same Compose network and use `http://api:8000` and
  `nats://nats:4222`.
- Data (database, snapshots, reference images) and secrets live in Docker volumes and survive
  `make down`. How the containers fit together, hardware profiles and Linux permissions are in
  [Deployment](../deployment.md).

**Working on Specter's code?** Run the processes from source instead, without Docker for them
(`make setup` once, then `make run-all`, `Ctrl+C` to stop). This does not restart a process that
crashes; `make run-api`, `make run-camera-manager` and `make run-detector` run them one by one. The
token is then `.dev/secrets/api.token` and the data is in `.dev/`. Do not run this and `make up` at
the same time: they use the same ports.

## Quick start

Install the clients (Node 22 or later):

```bash
npm install @nats-io/transport-node @nats-io/jetstream @nats-io/kv
```

### A small API client

```ts
// specter.ts
import { readFile } from "node:fs/promises";

const BASE_URL = process.env.SPECTER_API_URL ?? "http://127.0.0.1:8000";
const TOKEN = (await readFile(process.env.SPECTER_API_TOKEN_FILE ?? "/etc/specter/api.token", "utf8")).trim();

export class SpecterError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(`Specter answered ${status}: ${JSON.stringify(detail)}`);
    this.status = status;
    this.detail = detail;
  }
}

/** Calls the Specter API. Pass a `body` object for JSON, or a FormData for uploads. */
export async function specter<T>(
  path: string,
  { method = "GET", body }: { method?: string; body?: unknown } = {},
): Promise<T> {
  const headers: Record<string, string> = { Authorization: `Bearer ${TOKEN}` };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body; // fetch sets the multipart boundary itself
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const response = await fetch(`${BASE_URL}${path}`, { method, headers, body: payload });
  if (!response.ok) throw new SpecterError(response.status, await response.json().catch(() => null));
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}
```

### First call

```ts
import { specter } from "./specter.ts";

const OWNER = "acme";
const cameras = await specter<{ id: string; name: string; live_status: string | null }[]>(
  `/owners/${OWNER}/cameras`,
);
console.log(cameras);
```

An empty list `[]` means the connection and token work. A `401` means the token is wrong.

### First subscription

```ts
import { connect } from "@nats-io/transport-node";

const nc = await connect({ servers: "nats://127.0.0.1:4222", name: "app-server", maxReconnectAttempts: -1 });
for await (const message of nc.subscribe("specter.owners.acme.>")) {
  console.log(message.subject, message.json());
}
```

This is a **core** subscription, fine for looking at what flows but **not for alerts**: it loses
anything published while your server was down. The next recipes use JetStream instead.

## Generate types

Do not write the payload types by hand: generate them from Specter, so they cannot drift.

**HTTP API.** FastAPI serves its OpenAPI description, which needs no token:

```bash
npx openapi-typescript http://127.0.0.1:8000/openapi.json -o src/specter/api.d.ts
```

Every request and response shape is then typed. It does not cover the live-video WebSocket.

> `openapi-typescript` needs **TypeScript 5.x**. If your project already uses a newer TypeScript, it
> fails with `Cannot read properties of undefined (reading 'createKeywordTypeNode')`. Run it from a
> separate folder (`npx` installs a compatible TypeScript there) or pin `typescript@5` for the
> generation step. The directory of the output file must exist.

**NATS messages.** The repository keeps a JSON Schema per message in
[`contracts/jsonschema/`](../../contracts/jsonschema/):

```bash
npx json-schema-to-typescript --input contracts/jsonschema --output src/specter/events
```

Regenerate both when Specter is upgraded. The schemas are produced from the Python models with
`make contracts`, so they match what is really sent.

## Recipe: receive alerts

Use one **durable JetStream consumer** on the `EVENTS` stream. It remembers what you acknowledged,
so an alert published while your server was restarting is delivered when it comes back
([why](nats-events.md#consuming-reliably)).

```ts
import { connect } from "@nats-io/transport-node";
import { AckPolicy, DeliverPolicy, jetstream, jetstreamManager } from "@nats-io/jetstream";

const nc = await connect({ servers: "nats://127.0.0.1:4222", name: "app-server", maxReconnectAttempts: -1 });

// Creates the consumer on first run; on later runs it already exists and is reused.
const jsm = await jetstreamManager(nc);
await jsm.consumers.add("EVENTS", {
  durable_name: "app_alerts", // your own name; never reuse Specter's consumer names
  ack_policy: AckPolicy.Explicit,
  deliver_policy: DeliverPolicy.New, // only alerts from now on; use All for up to 7 days of history
  filter_subjects: [
    "specter.owners.*.cameras.*.match_confirmed",
    "specter.owners.*.cameras.*.rule_triggered",
  ],
});

const consumer = await jetstream(nc).consumers.get("EVENTS", "app_alerts");
for await (const message of await consumer.consume()) {
  try {
    const event = message.json<{ message_id: string; owner_id: string; camera_id: string }>();
    await handleAlert(message.subject, event); // your code: store, push notification, …
    message.ack();
  } catch (error) {
    console.error("cannot handle alert", message.subject, error);
    message.nak(5_000); // try again in 5 s; use message.term() to give up on a poison message
  }
}
```

What `handleAlert` should do:

1. **De-duplicate on `message_id`.** Delivery is at-least-once, so the same alert can arrive twice
   (for example when a `nak` or a lost ack causes a redelivery).
2. Read `owner_id`, find the users allowed to see it, and notify them.
3. **Tell match from rule by the subject** (`…match_confirmed` or `…rule_triggered`), then use the
   fields listed in the [message reference](nats-events.md#messages).
4. Fetch the picture **from the API**, not from `snapshot_path`:
   `GET /owners/{owner_id}/alerts/{message_id}/snapshot`. The alert id equals `message_id`.
   The alert is stored by another process a moment after the event, so **retry a `404` briefly**.

Acknowledge only *after* the alert is handled. If your process crashes in between, the alert is
delivered again rather than lost.

To catch up after a **long** outage (beyond the 7 days JetStream keeps), or to backfill history,
use `GET /owners/{owner}/alerts/identity-matches` and `…/rules` with `created_since`. Alerts stay in
the database.

## Recipe: follow camera status

Two complementary sources ([details](nats-events.md#camera-health-key-value-bucket)):

- `status_changed` **events**: what changed and when. Ideal for the UI.
- The `live_status` field on the camera, or the `camera_health` bucket: whether the camera is alive
  **right now**.

At startup, read the **last status of every camera**, and then keep following changes, with one
consumer:

```ts
import { DeliverPolicy, jetstream } from "@nats-io/jetstream";

const consumer = await jetstream(nc).consumers.get("CAMERA_STATUS", {
  filter_subjects: ["specter.owners.acme.cameras.*.status_changed"],
  deliver_policy: DeliverPolicy.LastPerSubject, // the latest status of each camera, then new ones
});

const status = new Map<string, string>(); // camera_id -> starting | running | reconnecting | stopped | failed
for await (const message of await consumer.consume()) {
  const event = message.json<{ camera_id: string; status: string }>();
  status.set(event.camera_id, event.status);
  // update the UI
}
```

`CAMERA_STATUS` keeps one message per camera, so this always gives the current picture.

To see that a camera is **alive**, watch the health bucket. Be aware that **an expired entry
produces no event**: keep the time of the last update per camera, and call it offline after about 10
seconds of silence.

```ts
import { Kvm } from "@nats-io/kv";

const health = await new Kvm(nc).open("camera_health");
const lastSeen = new Map<string, number>();
const watch = await health.watch({ key: "acme.>" }); // keys are "{owner_id}.{camera_id}"
for await (const entry of watch) {
  if (entry.operation === "PUT") lastSeen.set(entry.key, Date.now()); // sent about every 3 s
}
// elsewhere, every few seconds: offline = Date.now() - (lastSeen.get(key) ?? 0) > 10_000
```

If you only render a camera list on request, skip all of this and read `live_status` from
`GET /owners/{owner}/cameras`.

## Recipe: add a camera and start it

```ts
import { specter } from "./specter.ts";

const OWNER = "acme";

const camera = await specter<{ id: string }>(`/owners/${OWNER}/cameras`, {
  method: "POST",
  body: {
    name: "Front door",
    source_url: "rtsp://192.168.1.20:554/stream1", // no password in the URL
    credentials: { username: "admin", password: "secret" },
    watchlist_ids: [], // add watchlists to identify people
    detection_classes: ["person"], // empty = every object class
  },
});

await specter(`/owners/${OWNER}/cameras/${camera.id}/start`, { method: "POST" });
// 200 only means "the camera manager was asked". Wait for status_changed -> running.
```

Then, optionally:

```ts
// An area, then a rule that fires when someone stays 10 s in it
const zone = await specter<{ id: string }>(`/owners/${OWNER}/cameras/${camera.id}/zones`, {
  method: "POST",
  body: { name: "Driveway", polygon: [{ x: 0.1, y: 0.5 }, { x: 0.6, y: 0.5 }, { x: 0.6, y: 0.95 }, { x: 0.1, y: 0.95 }] },
});
await specter(`/owners/${OWNER}/cameras/${camera.id}/rules`, {
  method: "POST",
  body: { kind: "zone_occupancy", zone_id: zone.id, object_classes: ["person"], minimum_dwell_seconds: 10 },
});
```

Coordinates are fractions of the frame (0 to 1), so a UI can draw over a video of any size. A
`PATCH` to the camera restarts it; zone, rule and watchlist changes apply to a running camera
without a restart.

## Recipe: enroll targets with photos

A **watchlist** groups **targets** (a person, vehicle or object), each defined by reference photos.
Attach the watchlist to cameras with `watchlist_ids`.

```ts
import { readFile } from "node:fs/promises";
import { specter } from "./specter.ts";

const OWNER = "acme";

const watchlist = await specter<{ id: string }>(`/owners/${OWNER}/watchlists`, {
  method: "POST",
  body: { name: "Visitors", target_type: "person", kind: "watchlist" },
});

const files = ["jane1.jpg", "jane2.jpg"];
const form = new FormData();
form.set("targets", JSON.stringify([
  { label: "Jane", metadata: { crm_id: "42" }, image_file_names: files },
]));
for (const name of files) {
  form.append("images", new Blob([await readFile(name)], { type: "image/jpeg" }), name);
}

const targets = await specter<{ id: string; enrollment_status: string; enrollment_batch_id: string }[]>(
  `/owners/${OWNER}/watchlists/${watchlist.id}/targets`,
  { method: "POST", body: form },
);
// 201: photos accepted, status "queued". Not yet usable.
```

Every uploaded file must be named by exactly one target's `image_file_names`, and every named file
must be uploaded, or the whole request is refused (`422`) and nothing is stored.

**Enrollment is asynchronous.** The detector looks for a face in each photo afterwards. Follow the
result in either way:

- **Events:** listen to `specter.owners.{owner}.enrollment.status_changed`. It lives in the `EVENTS`
  stream, so add `"specter.owners.*.enrollment.status_changed"` to the `filter_subjects` of your
  alerts consumer, or create a second durable consumer for it. Each event is one embedding of one
  photo, `embedded` or `rejected` with a `rejection_reason`. After an event, re-read the target.
- **Polling:** `GET /owners/{owner}/enrollment-batches/{enrollment_batch_id}` until no target is
  `queued` or `partial`.

Finished states of a target are `ready` and `failed`. A rejected photo is usually a photo problem
(`no_face_detected`, `too_blurry`, `too_small`, `multiple_faces`, …), so show the reason and ask for
a better one. To add more photos later, `POST …/targets/{target_id}/images` with `images` parts only.

## Recipe: show and review alerts

```ts
type AlertPage = { alerts: { id: string; kind: "identity_match" | "rule" }[]; next_cursor: string | null };

// Newest first. Follow next_cursor until it is null.
let cursor: string | null = null;
do {
  const query = new URLSearchParams({ limit: "50", disposition: "unreviewed" });
  if (cursor) query.set("cursor", cursor);
  const page: AlertPage = await specter(`/owners/${OWNER}/alerts/identity-matches?${query}`);
  // render page.alerts
  cursor = page.next_cursor;
} while (cursor);
```

- Identity matches and rule alerts are **two separate lists** (`/identity-matches` and `/rules`).
  For a combined feed, fetch both and merge by `created_at`.
- The snapshot is a JPEG: `GET /owners/{owner}/alerts/{id}/snapshot`. Proxy it through your server;
  the browser has no token. `has_snapshot: false` means there is none (it may have been removed by
  the retention limit: 30 days or 5 GiB by default).
- Review: `POST …/acknowledge` (seen), or `POST …/resolve` with
  `{ "disposition": "true_positive" | "false_positive", "note": "…" }`.

## Recipe: live video

Your server relays it, because the browser cannot hold the token and cannot send headers on a
WebSocket. Pick MSE, HLS, WebRTC or single frames according to
[Live video](live-video.md). The simplest possible view, a refreshing picture:

```ts
// An Express-style route in your application server
app.get("/cameras/:id/frame.jpeg", requireLogin, async (req, res) => {
  const owner = ownerOf(req.user);           // your mapping
  await assertUserMayView(req.user, req.params.id);
  const upstream = await fetch(`${BASE_URL}/owners/${owner}/cameras/${req.params.id}/live/frame.jpeg`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  res.status(upstream.status).type("image/jpeg").send(Buffer.from(await upstream.arrayBuffer()));
});
```

## Going to production checklist

- [ ] The token is read from the file at startup and kept out of logs, browsers and git.
- [ ] Every request builds the owner id from your own user mapping, never from user input.
- [ ] Owner ids are lowercase letters, digits and underscores.
- [ ] Alerts arrive through a **durable consumer** with explicit acks, and are de-duplicated on
      `message_id`.
- [ ] Alert handling is idempotent (a redelivered alert is harmless) and acknowledges only after it
      succeeds.
- [ ] Your NATS connection uses `maxReconnectAttempts: -1`; Specter's own processes do the same, and
      NATS restarting must not need a restart of yours.
- [ ] You handle `503` (a service Specter needs is down) with retry and backoff.
- [ ] You do not assume a `2xx` from `start`, `create` or `upload` means *done*. Wait for the
      event.
- [ ] Start-up reads the current state (camera list, `CAMERA_STATUS` last values) instead of
      assuming everything is fresh.
- [ ] You ignore unknown fields in messages and check the major `schema_version`.
- [ ] NATS (4222) and the API (8000) are still bound to localhost.
- [ ] Whatever runs Specter restarts crashed processes (`make up` does; `make run-all` does not).

## Pitfalls

| Symptom | Cause |
|---|---|
| No alerts arrive after you restart | You used a core `subscribe`, or a consumer with the wrong start point. Use a durable consumer. |
| Alerts arrive twice | At-least-once delivery. De-duplicate on `message_id`. |
| Enrollment jobs never finish; photos stay `queued` | Something is consuming `ENROLLMENT_JOBS`, or the detector is not running. Never create consumers on that stream. |
| `404` for an id you just saw | It belongs to another owner, was deleted, or (for an alert) is not recorded yet: retry a `404` on a fresh alert. |
| `snapshot_path` cannot be opened | It is a path on the device. Use the API's `/snapshot` endpoint. |
| Camera `start` returns 200 but there is no video | The camera manager has not started it yet, or it cannot reach the camera. Watch `status_changed` and `live_status`. |
| Live video `409` | The camera was not started. `503`: it was just started and go2rtc has no stream yet; retry. |
| A camera is `null` in `live_status` | No process is reporting right now: not started, still starting, or crashed. |
| A second match of the same person does not appear | Cooldown: the same target on the same camera alerts at most once per 30 s by default. |
| Alerts for an owner never appear | The owner id contains a character NATS cannot use in a subject (`.`, `*`, `>`, whitespace). |
| `422` after adding a field | Unknown request fields are rejected on purpose. Check the field name against `/openapi.json`. |
| A deleted target's alerts still exist | By design: deleting a target, watchlist or camera keeps its alerts. Only deleting the owner removes them. |
