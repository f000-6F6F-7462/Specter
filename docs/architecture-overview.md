# Specter architecture overview

The diagram below shows the complete deployment topology. Specter runs on the edge device,
while the application server owns users and access control. The dashboard is optional; an
application server can integrate with Specter directly through its HTTP API and NATS events.

```mermaid
flowchart LR
    subgraph outside["Outside the edge device"]
        CAMERAS["IP cameras<br/>RTSP"]
        OPERATOR["Operator<br/>browser / mobile app"]
        CLOUD["Cloud application backend<br/>(Edge Agent mode)"]
    end

    subgraph device["Edge device"]
        subgraph product["Application layer (optional)"]
            CLIENT["Dashboard client<br/>React / Vite"]
            SERVER["Dashboard server<br/>users, notifications"]
        end

        subgraph engine["Specter engine"]
            API["api<br/>FastAPI :8000"]
            MANAGER["camera-manager<br/>camera supervision<br/>alert recording"]
            CAMERA["camera × N<br/>stream analysis"]
            DETECTOR["detector<br/>AI model service"]
            AGENT["agent<br/>cloud sync and relay"]
        end

        subgraph infra["Local infrastructure"]
            NATS[["NATS + JetStream<br/>events and work queues"]]
            QDRANT[("Qdrant<br/>face and appearance vectors")]
            GO2RTC["go2rtc<br/>stream restreaming<br/>WebRTC relay"]
        end

        DATA[("specter-data<br/>SQLite, snapshots,<br/>reference images")]
        FRAMES[("shared memory<br/>camera frames")]
        MODELS[("models volume<br/>ONNX / NCNN models")]
    end

    CAMERAS -->|"RTSP"| GO2RTC
    GO2RTC -->|"RTSP restream"| CAMERA
    CAMERA -->|"sampled frames"| FRAMES
    FRAMES --> DETECTOR
    DETECTOR -->|"detections and embeddings"| CAMERA
    CAMERA -->|"nearest-vector search"| QDRANT
    DETECTOR -->|"enrollment vectors"| QDRANT
    MODELS --> DETECTOR

    API -->|"configuration and enrollment jobs"| NATS
    NATS -->|"configuration changes"| MANAGER
    MANAGER -->|"spawn / supervise"| CAMERA
    MANAGER -->|"register streams"| GO2RTC
    CAMERA -->|"alerts and camera health"| NATS
    NATS -->|"durable alert events"| MANAGER
    MANAGER -->|"persist alerts and evidence"| DATA
    API -->|"read / write domain data"| DATA
    API -->|"live video signaling"| GO2RTC

    OPERATOR <-->|"HTTP / WebSocket"| CLIENT
    CLIENT <-->|"HTTP"| SERVER
    SERVER -->|"HTTP API"| API
    SERVER -->|"subscribe to events"| NATS
    OPERATOR -. "WebRTC media" .-> GO2RTC

    AGENT -->|"local HTTP API"| API
    AGENT -->|"local events"| NATS
    AGENT <-->|"Socket.IO<br/>camera/watchlist sync,<br/>offline alerts"| CLOUD
    AGENT -. "WebRTC signaling / relay" .-> CLOUD

    classDef external fill:#f8fafc,stroke:#64748b,color:#0f172a
    classDef app fill:#fef3c7,stroke:#d97706,color:#451a03
    classDef process fill:#dbeafe,stroke:#2563eb,color:#0f172a
    classDef infra fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef storage fill:#f3e8ff,stroke:#9333ea,color:#581c87

    class CAMERAS,OPERATOR,CLOUD external
    class CLIENT,SERVER app
    class API,MANAGER,CAMERA,DETECTOR,AGENT process
    class NATS,QDRANT,GO2RTC infra
    class DATA,FRAMES,MODELS storage
```

## Main paths

- **Video analysis:** cameras → go2rtc → camera processes → shared memory → detector → Qdrant.
- **Configuration:** dashboard/application server → HTTP API → NATS → camera-manager and cameras.
- **Alerts:** camera processes → NATS → camera-manager (durable recording) and application server
  (notifications).
- **Live video:** dashboard/application server negotiates through the API; media is delivered by
  go2rtc over WebRTC.
- **Edge Agent:** the local agent bridges the engine to a cloud backend and queues alerts while
  the cloud connection is unavailable.
