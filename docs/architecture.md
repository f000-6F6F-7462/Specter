# Specter architecture

Specter is the vision engine of the system. It reads camera streams, detects and identifies people
and objects, and turns what it sees into **alerts**. It has no user interface and no user accounts:
an **application server** sits in front of it, owns the users, and talks to Specter in exactly two
ways:

- an **HTTP API** for everything you *ask for or change* (cameras, zones, rules, watchlists,
  targets, reviewing alerts, live video);
- **NATS** for everything that *happens* (a match, a fired rule, a camera changing status, an
  enrollment finishing).

## 1. The big picture

```mermaid
flowchart LR
    subgraph outside["Outside the device"]
        IPCAM["IP cameras<br/>(RTSP)"]
        VIEWER["Operator's browser<br/>or mobile app"]
    end

    subgraph device["Specter device: everything below talks over localhost"]
        APP["<b>Application server</b><br/>(the code you write)"]

        subgraph python["Specter processes (Python)"]
            API["<b>api</b><br/>HTTP :8000"]
            CM["<b>camera-manager</b><br/>supervises cameras,<br/>records alerts"]
            CAMP["<b>camera</b> x N<br/>one process per camera"]
            DET["<b>detector</b><br/>runs the models"]
        end

        subgraph services["Services (Docker)"]
            NATS[["<b>NATS + JetStream</b><br/>:4222"]]
            QD[("Qdrant<br/>:6333<br/>face and appearance vectors")]
            G2["<b>go2rtc</b><br/>:1984 API, :8554 RTSP,<br/>:8555 WebRTC"]
        end

        DISK[("SQLite database,<br/>alert snapshots,<br/>reference images")]
    end

    IPCAM -- "RTSP" --> G2
    G2 -- "RTSP restream" --> CAMP
    CAMP <-- "frames: shared memory<br/>requests: NATS" --> DET
    CAMP -- "search vectors" --> QD
    DET -- "store vectors" --> QD
    CAMP -- "publish events" --> NATS
    CM -- "spawn and watch" --> CAMP
    CM -- "register streams" --> G2
    CM <-- "config changes,<br/>alerts to record" --> NATS
    API -- "publish config changes,<br/>enrollment jobs" --> NATS
    API -- "vectors" --> QD
    API -- "live video relay" --> G2
    python --- DISK

    APP == "HTTP + Bearer token" ==> API
    APP == "subscribe to events" ==> NATS
    VIEWER <--> APP
    VIEWER -. "WebRTC media" .-> G2

    classDef mine fill:#dbeafe,stroke:#1d4ed8,color:#0f172a
    class APP mine
```

What matters for you:

| You do | Through | Notes |
|---|---|---|
| Create or change cameras, zones, rules, watchlists, targets | `api` (HTTP) | Every change is announced on NATS so the running processes pick it up. |
| Learn that something happened | NATS | Alerts, camera status, enrollment results. |
| Show live video | `api` (HTTP and WebSocket) | The API relays go2rtc, so go2rtc itself never needs to be exposed. |
| Authenticate your users | **You** | Specter has one service token. It knows *owners*, not users. See [owners](integration/app-integration-guide.md#owners-and-isolation). |

## 2. The processes

| Process | Command | Job |
|---|---|---|
| `api` | `specter api` | Serves the HTTP API. Validates requests, stores changes, announces them on NATS. |
| `camera-manager` | `specter camera-manager` | Starts one `camera` process per camera that should run, restarts it if it dies or freezes, registers the camera's stream in go2rtc, publishes the camera's status, and **records every alert event into the database**. Also deletes old evidence. |
| `camera` | `specter camera --camera-id <id>` | Reads one camera, decides which frames to analyze, tracks objects, applies rules, identifies people, and publishes alert events. The camera manager starts it; you never do. |
| `detector` | `specter detector` | Serves the AI models to every camera process, and enrolls reference images (turns a photo into vectors). |

The services next to them (`deploy/compose.base.yaml`) all listen on `127.0.0.1`, except go2rtc's
WebRTC media port (`8555`), which viewers on the network must be able to reach.

## 3. What happens to a frame

```mermaid
flowchart TD
    A["go2rtc restream<br/>rtsp://127.0.0.1:8554/<i>camera_id</i>"] --> B["StreamReader<br/>decode, cap at 1920x1080"]
    B --> C{"FrameSampler<br/>is the frame due?<br/>(adaptive fps, motion gating)"}
    C -- "skip" --> B
    C -- "process" --> D["Write frame to the camera's<br/>shared memory region"]
    D --> E["detector: object detection<br/>(NATS request, replies with boxes)"]
    E --> F["Tracker<br/>stable track ids across frames"]
    F --> G["Rules engine<br/>zone occupancy + line crossing"]
    F --> H["Person identification<br/>best shot of each person"]
    H --> I["detector: face and appearance<br/>embeddings (NATS request)"]
    I --> J["Qdrant: nearest reference vectors<br/>of the camera's watchlists"]
    J --> K["Match policy<br/>threshold + margin + repeated confirmation"]
    K --> L{"Cooldown free?<br/>(match_cooldowns bucket)"}
    L -- "no: a recent alert exists" --> B
    L -- "yes" --> M["AlertPublisher"]
    G -- "a rule fired" --> M
    M --> N["Save JPEG snapshot<br/>to the evidence store"]
    N --> O["Publish match_confirmed<br/>or rule_triggered<br/>to NATS JetStream"]
```

Two consequences for the application:

- An alert is **only as fast as the pipeline**: expect the event a moment after the person is
  confirmed, not instantly.
- The same target on the same camera raises **at most one identity-match alert per cooldown**
  (30 seconds by default). Do not treat a missing second alert as a bug.

## 4. Flows you will implement

### 4.1 Adding a camera and starting it

```mermaid
sequenceDiagram
    autonumber
    participant APP as Application server
    participant API as api
    participant NATS as NATS
    participant CM as camera-manager
    participant G2 as go2rtc
    participant CAM as camera process

    APP->>API: POST /owners/{owner}/cameras
    API->>API: save the camera (stays stopped)
    API->>NATS: configuration.changed (camera, created)
    API-->>APP: 201 camera (live_status: null)

    APP->>API: POST /owners/{owner}/cameras/{id}/start
    API->>API: desired_state = running
    API->>NATS: configuration.changed (camera, updated)
    API-->>APP: 200 camera

    Note over CM: reacts to the change at once,<br/>and reconciles every 30 s anyway
    NATS-->>CM: configuration.changed
    CM->>G2: register the stream
    CM->>CAM: start the process
    CM->>NATS: status_changed (starting)
    NATS-->>APP: status_changed (starting)

    loop every 3 seconds
        CAM->>NATS: refresh camera_health bucket
    end
    CM->>NATS: status_changed (running)
    NATS-->>APP: status_changed (running)
```

A successful `start` means *"the camera manager was asked"*, not *"the camera is running"*. Watch
`status_changed` (or read `live_status` on the camera) to know when it actually is.

### 4.2 Adding targets with reference photos

```mermaid
sequenceDiagram
    autonumber
    participant APP as Application server
    participant API as api
    participant NATS as NATS
    participant DET as detector
    participant QD as Qdrant

    APP->>API: POST /owners/{owner}/watchlists/{id}/targets<br/>(multipart: targets JSON + image files)
    API->>API: store images, create targets (pending)
    API->>NATS: enrollment job, one per image and per embedding kind
    API->>NATS: configuration.changed (target, created)
    API-->>APP: 201 targets, enrollment_status "queued",<br/>enrollment_batch_id

    NATS-->>DET: next job (work queue, retried on failure)
    DET->>DET: quality checks, compute the embedding
    alt image is usable
        DET->>QD: store the vector
    else rejected (no face, blurry, ...)
        DET->>DET: record the rejection reason
    end
    DET->>NATS: enrollment.status_changed (embedded or rejected)
    NATS-->>APP: enrollment.status_changed

    APP->>API: GET /owners/{owner}/enrollment-batches/{batch_id}
    API-->>APP: targets with enrollment_status ready, partial or failed
```

Enrollment is asynchronous. The `201` response only says the photos were accepted; whether a face
was found is decided later by the detector. Show the batch as "processing" until the events say
otherwise.

### 4.3 An alert reaches the operator

```mermaid
sequenceDiagram
    autonumber
    participant CAM as camera process
    participant NATS as NATS (EVENTS stream)
    participant CM as camera-manager
    participant APP as Application server
    participant API as api
    participant OP as Operator's browser

    CAM->>CAM: save snapshot JPEG
    CAM->>NATS: match_confirmed or rule_triggered<br/>(message_id = alert id)

    par recorded for history
        NATS-->>CM: event (durable consumer)
        CM->>CM: save alert in the database
    and delivered to the app
        NATS-->>APP: event (your own durable consumer)
        APP->>OP: push notification / live update
    end

    OP->>APP: opens the alert
    APP->>API: GET /owners/{owner}/alerts/{alert_id}
    APP->>API: GET /owners/{owner}/alerts/{alert_id}/snapshot
    API-->>APP: alert JSON and JPEG

    OP->>APP: "false positive"
    APP->>API: POST /owners/{owner}/alerts/{alert_id}/resolve
```

The event carries everything needed for a notification. The **snapshot and the review state** come
from the API, because the recorder saves the alert a moment after the event is published. If the
first `GET` returns `404`, retry after a short delay.

## 5. NATS at a glance

Full reference: [NATS events](integration/nats-events.md).

```mermaid
flowchart LR
    subgraph pub["Publishers"]
        P1["camera process"]
        P2["camera-manager"]
        P3["api"]
        P4["detector"]
    end

    subgraph subjects["Subjects (specter.owners.{owner}...)"]
        S1["cameras.{camera}.match_confirmed"]
        S2["cameras.{camera}.rule_triggered"]
        S3["enrollment.status_changed"]
        S4["cameras.{camera}.status_changed"]
        S5["configuration.changed"]
        S6["specter.enrollment.jobs<br/>(internal)"]
    end

    subgraph streams["JetStream streams"]
        T1[["EVENTS<br/>7 days, 256 MiB"]]
        T2[["CAMERA_STATUS<br/>last one per camera"]]
        T3[["CONFIGURATION<br/>last one per owner"]]
        T4[["ENROLLMENT_JOBS<br/>work queue (internal)"]]
    end

    KV[("KV bucket camera_health<br/>expires after 10 s")]

    P1 --> S1 & S2
    P4 --> S3
    P2 --> S4
    P3 --> S5
    P3 --> S6
    S1 & S2 & S3 --> T1
    S4 --> T2
    S5 --> T3
    S6 --> T4
    P1 -. "refresh every 3 s" .-> KV

    classDef internal fill:#f1f5f9,stroke:#94a3b8,color:#475569,stroke-dasharray: 4 3
    class S6,T4 internal
```

The application server should consume `EVENTS`, `CAMERA_STATUS` and (optionally) `CONFIGURATION`, and
watch `camera_health`. It must **not** touch the `ENROLLMENT_JOBS` stream or the `specter.detector.*`
subjects: they are Specter's internal plumbing.

## 6. Data model

```mermaid
erDiagram
    OWNER ||--o{ CAMERA : has
    OWNER ||--o{ WATCHLIST : has
    CAMERA ||--o{ ZONE : "has areas"
    CAMERA ||--o{ RULE : "has rules"
    ZONE ||--o{ RULE : "zone occupancy rule watches"
    CAMERA }o--o{ WATCHLIST : "identifies against"
    WATCHLIST ||--o{ TARGET : contains
    TARGET ||--o{ REFERENCE_IMAGE : "is shown by"
    REFERENCE_IMAGE ||--|{ EMBEDDING : "enrolled as (face, appearance)"
    CAMERA ||--o{ ALERT : raises
    ALERT }o..o| TARGET : "identity match names"
    ALERT }o..o| RULE : "rule alert names"

    OWNER {
        string owner_id "chosen by you, never created explicitly"
    }
    CAMERA {
        string id "camera_ + 32 hex"
        string name
        string source_url
        bool is_enabled
        string desired_state "running or stopped"
    }
    ZONE {
        string id
        polygon points "3 or more, as fractions 0..1"
    }
    RULE {
        string kind "zone_occupancy or line_crossing"
        float minimum_dwell_seconds
        string direction "line_crossing only"
    }
    WATCHLIST {
        string target_type "person, vehicle or object: fixed"
        string kind "watchlist or blacklist"
        float face_match_threshold_ratio
        float appearance_match_threshold_ratio
    }
    TARGET {
        string label
        bool is_enabled
        string enrollment_status "queued, partial, ready, failed"
    }
    EMBEDDING {
        string modality "face or appearance"
        string status "pending, embedded, rejected"
    }
    ALERT {
        string id "same as the NATS message_id"
        string kind "identity_match or rule"
        string disposition "unreviewed, true_positive, false_positive"
    }
```

Things that follow from this model:

- **Alerts outlive what raised them.** Deleting a target, watchlist or camera keeps its alerts, so
  an alert can reference an id that no longer exists. Deleting the *owner* removes everything.
- **Zones, rules and coordinates are fractions of the frame** (0 to 1 from the top-left), never
  pixels, so they do not depend on the camera's resolution.
- A watchlist's **target type cannot change** after creation.

## 7. Where things live on disk

Paths are the same inside every container. In Docker they are volumes; from source they are under
`.dev/`. See [Deployment](deployment.md).

| What | Path (Docker / from source) |
|---|---|
| Static settings | environment variables in `deploy/compose.yaml` / `config/specter.dev.yaml` |
| Database, snapshots, reference images | volume `specter-data` at `/var/lib/specter` / `.dev/data` |
| API token and camera credentials key | volume `specter-secrets` at `/etc/specter` / `.dev/secrets` |
| AI models | `./models` at `/opt/specter/models` / `./models` |
| Frames shared between camera processes and the detector | volume `specter-frames` (memory) at `/dev/shm` |
| Message contracts (JSON Schema) | `contracts/jsonschema/` |
