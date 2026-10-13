# Android Agent MVP Plan

## Goal

Add an Android 15 camera agent that can use a smartphone camera as a Specter
security camera and report detections through the existing Dashboard.

The first version should be KISS: periodic JPEG uploads instead of continuous
video streaming.

## Agent types

Keep a shared Agent concept with separate implementations:

- Local Python Agent for USB and RTSP cameras on a customer's local network.
- Android Camera Agent for a smartphone camera.
- Future Windows/macOS Agent.

## Android MVP behavior

The Android application should:

1. Request camera permission.
2. Pair with Specter using a one-time pairing token.
3. Show a camera preview.
4. Start and stop monitoring explicitly.
5. Capture JPEG frames periodically.
6. Upload frames to Specter over HTTPS.
7. Show Connected, Uploading, Offline, and permission-required states.
8. Continue monitoring in a foreground service.
9. Keep at most one pending frame locally while offline.
10. Stop or reduce activity when the battery is low.

## Adaptive upload rate

Start with one frame every two seconds. Adjust the interval using upload
latency, server response time, consecutive failures, and network availability:

- Fast network: approximately one frame every 0.5–1 second.
- Normal network: approximately one frame every 2 seconds.
- Slow network: approximately one frame every 5–10 seconds.
- Offline: pause uploads and retry with exponential backoff.

The implementation must include upper and lower bounds so that network
conditions cannot cause an uncontrolled upload rate.

## Backend contract

The existing Specter camera API expects a continuous camera source through
go2rtc. Android periodic JPEG uploads therefore need a dedicated frame-ingest
contract rather than pretending that the phone is an RTSP source.

Proposed endpoint:

```text
POST /agent/cameras/{camera_id}/frames
Content-Type: image/jpeg
Authorization: Bearer <agent-token>
```

The endpoint should:

- Authenticate a dedicated Agent token.
- Enforce tenant and camera ownership.
- Validate content type and image size.
- Reject stale or malformed requests explicitly.
- Pass the frame into the existing detection and alert pipeline.
- Reuse existing face matching, watchlists, evidence, and alert behavior.

## Pairing and authentication

Do not use a general Specter API token in the APK.

The intended flow is:

1. The user creates an Agent in the Dashboard.
2. The Dashboard creates a short-lived, one-time pairing token.
3. The user enters or scans the pairing token in the Android app.
4. The server exchanges it for a dedicated, revocable Agent token.
5. The app stores the token using Android secure storage.
6. The user can revoke the Agent from the Dashboard.

Tokens must never be logged or committed to source control.

## Suggested Android stack

- Kotlin
- Android 15
- CameraX
- Jetpack Compose
- Foreground Service
- HTTPS client
- JPEG compression
- Secure token storage
- Retry and exponential-backoff logic

Avoid WebRTC, RTSP, and Socket.IO in the first version. They are not needed
for periodic detection frames and would add unnecessary MVP complexity.

## Initial user-facing MVP

- Install APK.
- Pair the phone with a Specter account.
- See a camera preview.
- Tap `Start monitoring`.
- Receive periodic face/object analysis.
- See alerts and snapshots in the existing Dashboard.
- See Online/Offline state.
- Stop monitoring from the app.

## Later phases

After the periodic-frame MVP is stable:

- Improve adaptive rate and battery policies.
- Add remote configuration from the Dashboard.
- Add Agent heartbeat and health details.
- Add multiple phone cameras per account.
- Add continuous Live view using WebRTC if required.
- Add production packaging and distribution.

## Proposed repository location

The future Android project should live separately under:

```text
agent/android/
```

The backend frame-ingest implementation should remain in the existing Specter
API and Dashboard server boundaries, with contracts and tests updated together.

