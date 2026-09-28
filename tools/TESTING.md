# Testing face recognition end to end

A self-contained runbook: from a fresh checkout to a verified real-time face-recognition alert,
and the tests that tell you whether it is good enough for your deployment.

Nothing here is specific to one machine. Section 0 collects everything that differs between
machines into a handful of shell variables; the rest of the guide only ever uses those.

**Supported:** macOS and Linux, with any Docker runtime. The helper scripts are bash and use
`avfoundation` (macOS) or `v4l2` (Linux), so Windows needs WSL2.

---

## 0. Adapt to your machine

Set these once per terminal. Every later command uses them, so nothing below needs editing.

```bash
export REPO="$HOME/Project/Specter"      # where you cloned Specter
cd "$REPO"

export BASE=http://127.0.0.1:8000        # the API; change only if you moved the port
export OWNER=demo                        # any string you like; it is created on first use
export CAM="0"                           # your capture device — see 3.1 for how to find it
export PHOTOS="$HOME/Pictures/enroll"    # a folder holding 2-3 photos of the person
```

`$CAM` is the one you must look up: on macOS it is a **device name** such as `"Brio 300"`, on
Linux a path such as `/dev/video0`.

**Already have a real IP camera?** Then skip the entire host-streaming layer. Sections 3.1 and the
`host.docker.internal` problem do not apply — set `SOURCE` to the camera's own RTSP URL in 3.6 and
go straight to 3.2. Everything else is identical.

---

## 1. How the pieces fit, and why it matters

Specter's camera process **never opens the camera itself**. The camera manager registers the
camera's `source_url` in go2rtc, and the camera process reads the stream back *from go2rtc*
(`src/specter/camera/main.py`). go2rtc runs in a container and cannot see a local USB device, so
on a laptop something on the host has to serve RTSP to it:

```
camera → ffmpeg → mediamtx (host :18554) → go2rtc (container :8554) → camera process → alert
```

Two consequences cause most first-run failures:

- **`source_url` must be reachable from inside a container**, not from your shell. `127.0.0.1` in
  a container is the container. Which address to use instead depends on your Docker runtime —
  section 3.1 has a command that determines it rather than guessing.
- **Port 18554, not 8554**, because the go2rtc container already publishes 8554.

An IP camera on the network avoids both, which is why the real deployment is simpler than this
test rig.

---

## 2. Prerequisites

| | macOS | Linux |
|---|---|---|
| Stream tools | `brew install ffmpeg mediamtx` | `apt install ffmpeg v4l-utils` (or `dnf`), mediamtx from [releases](https://github.com/bluenviron/mediamtx/releases) |
| `jq`, `curl` | `brew install jq` | `apt install jq curl` |
| Docker | Docker Desktop, Colima, OrbStack… | Docker Engine, Docker Desktop, Podman |
| Models | `make models` — **once**, ~250 MB into `./models` | same |
| Python env | `make setup` — only needed for `tools/watch-alerts.py` | same |

`make models` writes to `./models`, a plain directory, not a Docker volume. It therefore survives
`docker compose down -v`; you rerun it only if you delete that directory.

**Hardware profile.** The stack defaults to `pc-cpu`, which runs ONNX models on the CPU and works
everywhere. If the machine has an accelerator, set it before `make up`:

```bash
export SPECTER_HARDWARE_PROFILE=pc-nvidia     # or jetson, raspberry-pi
```

The profile changes which models load and therefore the latency figures in section 5. `make models`
produces the files for every profile, so you do not need to rerun it when you switch.

---

## 3. Start, with a verification gate at every step

Each gate exists because skipping one means discovering the failure three steps later and blaming
the wrong component. **Do not continue past a failed gate.**

### 3.1 Camera → RTSP (terminal 1)

*Skip this whole step if you are using an IP camera.*

Find your device:

```bash
ffmpeg -f avfoundation -list_devices true -i ""   # macOS: note the NAME, not the number
v4l2-ctl --list-devices                           # Linux: note the /dev/videoN path
```

**On macOS use the device name, never the index.** avfoundation renumbers devices between runs —
a camera that is `[1]` now can be `[0]` a minute later, and you would silently capture the
built-in camera instead. Linux `/dev/videoN` paths are stable.

```bash
./tools/usb-rtsp.sh "$CAM"
./tools/usb-rtsp.sh "$CAM" --modes        # if ffmpeg rejects the resolution
SIZE=640x480 FPS=15 ./tools/usb-rtsp.sh "$CAM"
```

The script detects your platform and Docker runtime and **prints the `source_url` to give
Specter**. Copy it. Leave the script running.

> **Gate 1a — the stream exists on the host**
> ```bash
> ffprobe -rtsp_transport tcp rtsp://127.0.0.1:18554/usb
> ```
> Must show `Video: h264 ...`. If not, nothing downstream can work.

> **Gate 1b — a container can reach it.** This is the step that differs per machine, so test it
> rather than trusting a rule of thumb. With the stream running:
> ```bash
> for host in host.docker.internal host.containers.internal \
>             "$(docker network inspect bridge \
>                --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}' 2>/dev/null)"; do
>   [ -z "$host" ] && continue
>   printf '%-28s ' "$host"
>   docker run --rm alpine:3.20 sh -c \
>     "nc -z -w 3 $host 18554 && echo REACHABLE || echo no" 2>/dev/null | tail -1
> done
> ```
> Use the first address that prints `REACHABLE` as the host part of `SOURCE` in step 3.6. On
> Docker Desktop that is normally `host.docker.internal`; on native Linux Docker it is the gateway
> address; on Podman it is `host.containers.internal`.

### 3.2 The stack (terminal 2)

```bash
cd "$REPO" && make up
```

> **Gate 2**
> ```bash
> curl -s "$BASE/health" | jq
> ```
> Must show `"status":"ok"` and `"is_nats_connected":true`. Allow 20–40 s on first start while the
> detector loads models — longer on a slow disk.

<details>
<summary>Running from source instead of Docker</summary>

The `specter` CLI runs each process on the host. Do not run it together with `make up`; they use
the same ports.

```bash
docker compose -f deploy/compose.base.yaml up -d        # NATS, Qdrant and go2rtc only
uv run specter --config config/specter.dev.yaml migrate
uv run specter --config config/specter.dev.yaml api     # then camera-manager, then detector
```

The only difference that matters below is the token, which then lives at `.dev/secrets/api.token`:

```bash
export AUTH="Authorization: Bearer $(cat .dev/secrets/api.token)"
```

go2rtc still runs in Docker this way, so Gate 1b still applies.
</details>

### 3.3 Authentication (terminal 3)

```bash
cd "$REPO"                                # make must run from the repo root
export AUTH="Authorization: Bearer $(make api-token)"
```

> **Gate 3**
> ```bash
> echo "${#AUTH}"     # about 65. If it is 22, the token is empty.
> ```
> **Re-export `$AUTH` after every `down -v`.** That deletes `specter-secrets`, Specter generates a
> new token on the next start, and your shell variable still holds the old one. This is the single
> most common cause of a mysterious `null`.

**Add `-w '\n%{http_code}\n'` to every curl.** `jq -r .id` prints `null` for 401, 404 and 422
alike; the status code tells them apart, and will save you an hour.

### 3.4 Watchlist

```bash
export WATCHLIST=$(curl -s -X POST "$BASE/owners/$OWNER/watchlists" \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"name":"Blacklist","target_type":"person","kind":"blacklist",
       "face_match_threshold_ratio":0.45}' | jq -r .id)
echo "$WATCHLIST"
```

`kind` is a label your application interprets; Specter matches `blacklist` and `watchlist`
identically. `face_match_threshold_ratio` defaults to `0.45` — **higher is stricter**.

Reusing an existing watchlist instead:

```bash
export WATCHLIST=$(curl -s "$BASE/owners/$OWNER/watchlists" -H "$AUTH" | jq -r '.[0].id')
```

> **Gate 4** — `$WATCHLIST` starts with `watchlist_`, not `null`.

### 3.5 Reference photos

Put 2–3 clear, frontal, well-lit photos of one person in `$PHOTOS`. The `@` path is resolved by
curl on your machine; the server only ever receives the **basename**, so `image_file_names` lists
bare file names, never paths.

Every `.jpg` in the folder, without typing the names — arrays and `set --` behave the same in bash
and zsh, which plain word splitting does not:

```bash
cd "$PHOTOS"
set -- *.jpg
JSON=$(printf '%s\n' "$@" | jq -R . | jq -sc '{label:"Me", image_file_names:.}')
ARGS=()
for f in "$@"; do ARGS+=(-F "images=@$f;type=image/jpeg"); done

curl -s -w '\n%{http_code}\n' -X POST "$BASE/owners/$OWNER/watchlists/$WATCHLIST/targets" \
  -H "$AUTH" -F "targets=[$JSON]" "${ARGS[@]}" | jq
cd "$REPO"
```

Or spell it out, which is clearer when there are only two photos:

```bash
curl -s -w '\n%{http_code}\n' -X POST "$BASE/owners/$OWNER/watchlists/$WATCHLIST/targets" \
  -H "$AUTH" \
  -F 'targets=[{"label":"Me","image_file_names":["a.jpg","b.jpg"]}]' \
  -F "images=@$PHOTOS/a.jpg;type=image/jpeg" \
  -F "images=@$PHOTOS/b.jpg;type=image/jpeg" | jq
```

Accepted types are `image/jpeg`, `image/png` and `image/webp`, up to 10 MiB each.

> **Gate 5 — the one people skip.** `201` does **not** mean the photos are usable; the detector
> still has to find a face in them.
> ```bash
> curl -s "$BASE/owners/$OWNER/watchlists/$WATCHLIST/targets" -H "$AUTH" \
> | jq '.[] | {enrollment_status,
>     e:[.reference_images[].embeddings[]|{modality,status,rejection_reason}]}'
> ```
> Wait for `"enrollment_status":"ready"` with `face` → `"embedded"`. On `rejected`, read
> `rejection_reason` and use a better photo. Matching cannot work without a face embedding.

### 3.6 Camera

Use the address Gate 1b proved reachable — or your IP camera's own URL:

```bash
export SOURCE="rtsp://host.docker.internal:18554/usb"    # whatever Gate 1b confirmed

export CAMERA=$(curl -s -X POST "$BASE/owners/$OWNER/cameras" \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"name\":\"Test\",\"source_url\":\"$SOURCE\",
       \"watchlist_ids\":[\"$WATCHLIST\"],\"detection_classes\":[\"person\"],
       \"sampling\":{\"mode\":\"fixed\",\"target_fps\":10,\"minimum_fps\":3,
                     \"is_motion_gating_enabled\":false}}" | jq -r .id)

curl -s -X POST "$BASE/owners/$OWNER/cameras/$CAMERA/start" -H "$AUTH" \
| jq '{desired_state,live_status}'
```

If the camera needs credentials, send them separately — a password inside `source_url` is
rejected with 422:

```bash
  ... ,\"credentials\":{\"username\":\"admin\",\"password\":\"secret\"} ...
```

Three deliberate choices: `watchlist_ids` is **required** or no identification happens at all;
`detection_classes` must contain `person`; fixed sampling with motion gating off keeps test runs
deterministic.

> **Gate 6 — check both sides.** `live_status` alone has been misleading.
> ```bash
> sleep 8
> curl -s "$BASE/owners/$OWNER/cameras/$CAMERA" -H "$AUTH" | jq '.live_status'
> curl -s "http://127.0.0.1:1984/api/streams?src=$CAMERA" \
> | jq '{url:.producers[0].url, recv:.producers[0].bytes_recv, consumers:(.consumers|length)}'
> ```
> Want `"running"`, and `bytes_recv` **growing** between two runs. A frozen counter means go2rtc is
> not pulling — revisit Gate 1b and `SOURCE`.

---

## 4. Seeing the alerts

Three views of the same thing. An application server uses the first two.

**Push — what a real integration subscribes to:**

```bash
cd "$REPO" && uv run python tools/watch-alerts.py "$OWNER"
```

Prints each alert the moment Specter publishes it. In production the app must use a **durable
JetStream consumer**, or alerts raised while it was down are lost; JetStream retains them 7 days.

**Pull — HTTP:**

```bash
curl -s "$BASE/owners/$OWNER/alerts/identity-matches?limit=10" -H "$AUTH" | jq
```

On `main` the endpoints are `/alerts/identity-matches` and `/alerts/rules`. A plain `/alerts` or
`/alerts/summary` returns **404** — those exist only on the merged-alerts branch.

**Browser:** open `$BASE/docs` → **Authorize** → paste `make api-token` → try
`GET /owners/{owner_id}/alerts/identity-matches`.

**Snapshot of an alert:**

```bash
ID=$(curl -s "$BASE/owners/$OWNER/alerts/identity-matches?limit=1" -H "$AUTH" | jq -r '.alerts[0].id')
curl -s "$BASE/owners/$OWNER/alerts/$ID/snapshot" -H "$AUTH" -o /tmp/alert.jpg
open /tmp/alert.jpg 2>/dev/null || xdg-open /tmp/alert.jpg
```

**Timestamps are UTC**, because Specter stores every timestamp in UTC by design. Containers run
UTC and your machine probably does not, so a three-hour gap is a timezone, not a bug:

```bash
curl -s "$BASE/owners/$OWNER/alerts/identity-matches?limit=10" -H "$AUTH" \
| jq -r '.alerts[] | "\(.created_at) sim=\(.similarity_ratio) track=\(.track_id)"' \
| python3 -c "
import sys, datetime
for line in sys.stdin:
    stamp, rest = line.split(' ', 1)
    print(datetime.datetime.fromisoformat(stamp).astimezone().strftime('%H:%M:%S'), rest.strip())
"
```

---

## 5. What correct behaviour looks like

Most "it is broken" reports are this model working as designed. Read this before testing.

### One alert per track

A person standing still produces **one** alert, then silence. Four mechanisms stack up:

| Mechanism | Value | Where |
|---|---|---|
| A track ends after this long unseen | **2.0 s** | `vision/tracking.py` |
| A track is sampled at most every | **0.4 s** | `vision/best_shot.py` |
| A track stops being sampled **forever** after | **20 embeddings** | `vision/best_shot.py` |
| One alert per camera+target per | **30 s** | `matching.cooldown_seconds` |

A track spends its 20 embeddings within seconds and then goes inert — no further identification on
it, ever. **A new alert requires a new track**, which requires the tracker to lose the person for
2 seconds. If you need a continuous "this person is still here" signal, this model does not
provide one.

### The quality gate

Every face must pass all of these before recognition is even attempted
(`RUNTIME_QUALITY_THRESHOLDS` in `vision/quality.py`):

| Gate | Limit | Rejection |
|---|---|---|
| Detection score | ≥ 0.4 | `LOW_DETECTION_SCORE` |
| Blur | ≤ 0.75 | `TOO_BLURRY` |
| Face height | ≥ 32 px | `TOO_SMALL` |
| **Yaw (head turn)** | **≤ 55°** | `EXTREME_POSE` |
| Brightness | 0.2–0.9 | `TOO_DARK` / `TOO_BRIGHT` |

**Specter trades recall for precision.** It refuses to identify a profile face because ArcFace
embeddings at extreme angles are unreliable and produce false positives. For a blacklist that is
usually the right trade — but it means **a person walking past without turning toward the camera
will not be recognised**, which dictates where you mount the camera.

### Reference figures — and how to get your own

These came from one machine: Apple Silicon, `pc-cpu` profile, 1280x720 @ 10 fps, USB webcam. Treat
them as an order of magnitude, not a specification. A GPU profile, a different resolution or a
busier machine will move them.

| Metric | Reference |
|---|---|
| Pipeline: frame captured → alert published | 300–380 ms |
| Delivery: published → received over NATS | ~15 ms |
| Appearing in frame → identified | ~2.4 s |
| Capture rate, walking past **not** facing the camera | 4 / 10 |

**Measure your own** — the numbers are in every `match_confirmed` event, so no instrumentation is
needed beyond subtracting its timestamps:

| Your metric | Compute from the event |
|---|---|
| Pipeline latency | `occurred_at` − `frame_captured_at` |
| Delivery latency | now − `occurred_at`, at the moment you receive it |
| Appearing → identified | `occurred_at` − `first_seen_at` |

The last one matters operationally: if it is ~2 s and a walking pass also lasts ~2 s, the alert
lands as the person leaves the frame. Fine for "tell me who came in"; not fine for "stop them at
the door".

---

## 6. Test protocols

Latency is the easy number. **What decides whether this is deployable is the capture rate**, so
every test counts hits against a known denominator. Count your own passes — without that number
the result means nothing.

Run `tools/watch-alerts.py` in its own terminal, and **cross-check against the HTTP list
afterwards**. A watcher that has quietly died looks exactly like a system that found nothing; that
mistake turned a 4-of-10 result into an apparent 0-of-10 during the writing of this guide.

| Test | Do this | What it tells you |
|---|---|---|
| **A. Walk past** | 10 passes at walking speed, not looking at the camera, fully leaving frame 5 s between | The realistic baseline |
| **B. Walk past, facing** | Same, but face the camera while crossing | Isolates head angle from motion blur |
| **C. Distance** | Passes at 1, 2, 3, 4 m | Where recognition stops — sets mounting distance |
| **D. Angle** | Profile, 45°, back turned | Confirms the 55° yaw gate in practice |
| **E. Negative** | 5 min empty room, then a **different person** | False positives. Run this before trusting any other result |
| **F. Lighting** | Dim, and backlit | Stability outside lab conditions |

**Run E first.** A system that alerts on everybody produces a log indistinguishable from one that
works, and every capture rate you measure afterwards is meaningless if E fails.

Interpreting a capture rate: **9–10** is reliable; **6–8** works but misses, so tune placement or
threshold; **≤ 5** means the condition you are testing is outside what this configuration handles.

If a test yields zero, verify the pipeline before concluding anything:

```bash
curl -s "$BASE/owners/$OWNER/cameras/$CAMERA" -H "$AUTH" | jq '.live_status'
curl -s "http://127.0.0.1:1984/api/streams?src=$CAMERA" | jq '.producers[0].bytes_recv'
pgrep -f ffmpeg >/dev/null && echo publishing || echo STOPPED
docker compose -f deploy/compose.base.yaml -f deploy/compose.yaml logs --since 5m camera-manager
```

Tuning knobs, easiest first: `face_match_threshold_ratio` on the watchlist (applies with no camera
restart), then camera placement and lighting, then `SIZE`/`FPS` in `tools/usb-rtsp.sh`, then the
hardware profile. The 20-embedding cap and the quality gates are constants in the source, not
settings.

```bash
curl -s -X PATCH "$BASE/owners/$OWNER/watchlists/$WATCHLIST" -H "$AUTH" \
  -H 'Content-Type: application/json' -d '{"face_match_threshold_ratio":0.35}'
```

---

## 7. Troubleshooting

| Symptom | Cause |
|---|---|
| `jq` prints `null` | Empty token: `make` ran outside the repo, or `$AUTH` is stale after `down -v` |
| `401` | Same. With `make up` the token is `make api-token`; run from source it is `.dev/secrets/api.token` |
| `404` on `/alerts` | Use `/alerts/identity-matches`. `/alerts` exists only on the merged-alerts branch |
| `live_status` `reconnecting` / `failed` | go2rtc cannot reach the source. Rerun Gate 1b and fix `SOURCE` |
| Stream 404 in mediamtx | ffmpeg exited — usually the camera is held by another application |
| `Cannot use <camera>` | Another process holds it, often a leftover ffmpeg: `pkill -f ffmpeg` |
| Captured the wrong camera | You passed an index on macOS. Use the device **name** |
| `enrollment_status: failed` | Read `rejection_reason`; the photo has no usable face |
| One alert then silence | Correct behaviour. Same track, 20 embeddings spent. Needs a new track |
| Times look hours off | Containers are UTC, your machine is not |
| Ports already in use | `make up` and a from-source run together, or another service on 8000/4222/6333/1984/8554/18554 |
| Detector container exits | A model file is missing — rerun `make models` and check `ls models` |

---

## 8. Reset

```bash
cd "$REPO"

# stop
pkill -f usb-rtsp.sh
make down                     # keeps all data

# wipe one owner's data, stack still running
curl -s -X DELETE "$BASE/owners/$OWNER" -H "$AUTH" -w 'HTTP %{http_code}\n'

# full reset: database, secrets, vectors. The token changes -> re-export $AUTH afterwards
docker compose -f deploy/compose.base.yaml -f deploy/compose.yaml down -v
```

`./models` is a directory, not a volume, so a full reset never costs you `make models`.

Verify the machine is clean:

```bash
docker ps -a --format '{{.Names}}' | grep -i specter || echo "no containers"
docker volume ls --filter name=specter --format '{{.Name}}' | grep . || echo "no volumes"
pgrep -fl "mediamtx|ffmpeg|usb-rtsp" || echo "no stream processes"
```
