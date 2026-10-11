"""Synchronize locally reachable cameras with a Specter API."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


class AgentError(RuntimeError):
    """Raised when the agent cannot complete an operation."""


@dataclass(frozen=True)
class CameraConfig:
    """A camera configured for the local agent."""

    name: str
    source_url: str
    credentials: dict[str, str] | None
    detection_classes: list[str]


@dataclass(frozen=True)
class AgentConfig:
    """Configuration required to connect to a local Specter API."""

    owner_id: str
    api_token_file: Path
    cameras: list[CameraConfig]


def load_config(path: Path) -> AgentConfig:
    """Load and validate the agent configuration."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AgentError(f"cannot read configuration: {error}") from error

    if not isinstance(raw, dict):
        raise AgentError("configuration must be a JSON object")
    owner_id = raw.get("owner_id")
    token_file = raw.get("api_token_file")
    raw_cameras = raw.get("cameras")
    if not isinstance(owner_id, str) or not owner_id:
        raise AgentError("owner_id must be a non-empty string")
    if not isinstance(token_file, str) or not token_file:
        raise AgentError("api_token_file must be a non-empty string")
    if not isinstance(raw_cameras, list) or not raw_cameras:
        raise AgentError("cameras must contain at least one camera")

    cameras: list[CameraConfig] = []
    for index, raw_camera in enumerate(raw_cameras, start=1):
        if not isinstance(raw_camera, dict):
            raise AgentError(f"cameras[{index}] must be an object")
        name = raw_camera.get("name")
        source_url = raw_camera.get("source_url")
        credentials = raw_camera.get("credentials")
        detection_classes = raw_camera.get("detection_classes", [])
        if not isinstance(name, str) or not name.strip():
            raise AgentError(f"cameras[{index}].name must be a non-empty string")
        if not isinstance(source_url, str) or not source_url.strip():
            raise AgentError(f"cameras[{index}].source_url must be a non-empty string")
        if credentials is not None and (
            not isinstance(credentials, dict)
            or not isinstance(credentials.get("username"), str)
            or not isinstance(credentials.get("password"), str)
        ):
            raise AgentError(f"cameras[{index}].credentials is invalid")
        if not isinstance(detection_classes, list) or not all(
            isinstance(item, str) and item.strip() for item in detection_classes
        ):
            raise AgentError(f"cameras[{index}].detection_classes is invalid")
        cameras.append(
            CameraConfig(
                name=name.strip(),
                source_url=source_url.strip(),
                credentials=credentials,
                detection_classes=detection_classes,
            )
        )
    return AgentConfig(owner_id, Path(token_file), cameras)


class SpecterAgent:
    """Synchronize and operate the cameras configured for this machine."""

    def __init__(self, config: AgentConfig, api_url: str) -> None:
        self.config = config
        self.api_url = api_url.rstrip("/")
        try:
            token = config.api_token_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise AgentError(f"cannot read API token file: {error}") from error
        if not token:
            raise AgentError("API token file is empty")
        self.client = httpx.Client(
            base_url=self.api_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self.client.close()

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Send an authenticated request and return its JSON payload."""
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise AgentError(f"Specter API is unreachable: {error}") from error
        if not response.is_success:
            detail: object
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise AgentError(f"Specter API returned {response.status_code}: {detail}")
        if response.status_code == 204:
            return None
        return response.json()

    def cameras(self) -> list[dict[str, Any]]:
        """Read all cameras owned by this agent."""
        payload = self.request("GET", f"/owners/{self.config.owner_id}/cameras")
        if not isinstance(payload, list):
            raise AgentError("Specter API returned an invalid camera list")
        return payload

    def sync(self) -> None:
        """Create missing cameras and update matching camera settings."""
        existing = {camera.get("name"): camera for camera in self.cameras()}
        for camera in self.config.cameras:
            body = {
                "name": camera.name,
                "source_url": camera.source_url,
                "credentials": camera.credentials,
                "detection_classes": camera.detection_classes,
            }
            current = existing.get(camera.name)
            if current is None:
                self.request(
                    "POST",
                    f"/owners/{self.config.owner_id}/cameras",
                    json=body,
                )
                print(f"Created camera: {camera.name}")
                continue
            camera_id = current.get("id")
            if not isinstance(camera_id, str):
                raise AgentError(f"camera {camera.name!r} has no valid id")
            self.request(
                "PATCH",
                f"/owners/{self.config.owner_id}/cameras/{camera_id}",
                json={
                    "source_url": camera.source_url,
                    "credentials": camera.credentials,
                    "detection_classes": camera.detection_classes,
                },
            )
            print(f"Updated camera: {camera.name}")

    def start(self) -> None:
        """Start every configured camera."""
        cameras = {camera.get("name"): camera for camera in self.cameras()}
        for configured in self.config.cameras:
            camera = cameras.get(configured.name)
            camera_id = camera.get("id") if camera else None
            if not isinstance(camera_id, str):
                raise AgentError(f"camera {configured.name!r} does not exist; run sync first")
            self.request(
                "POST",
                f"/owners/{self.config.owner_id}/cameras/{camera_id}/start",
            )
            print(f"Started camera: {configured.name}")

    def status(self) -> None:
        """Print non-sensitive status for every configured camera."""
        cameras = {camera.get("name"): camera for camera in self.cameras()}
        for configured in self.config.cameras:
            camera = cameras.get(configured.name)
            if camera is None:
                print(f"{configured.name}: missing")
                continue
            print(
                f"{configured.name}: {camera.get('live_status') or 'stopped'} "
                f"(desired={camera.get('desired_state', 'unknown')})"
            )

    def heartbeat(self) -> None:
        """Report this agent and its configured camera names to the cloud."""
        payload = {
            "version": "0.1.0",
            "cameras": [{"name": camera.name, "status": "unknown"} for camera in self.config.cameras],
        }
        response = self.request("POST", "/api/agent/heartbeat", json=payload)
        if not isinstance(response, dict) or response.get("success") is not True:
            raise AgentError("cloud heartbeat returned an invalid response")
        print("Cloud heartbeat accepted")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "command",
        choices=("sync", "start", "status", "heartbeat"),
        help="operation to perform",
    )
    return parser.parse_args()


def main() -> int:
    """Run the selected agent operation."""
    args = parse_args()
    try:
        config = load_config(args.config)
        api_url = os.environ.get("SPECTER_AGENT_API_URL", "http://127.0.0.1:8000")
        agent = SpecterAgent(config, api_url)
        try:
            getattr(agent, args.command)()
        finally:
            agent.close()
    except AgentError as error:
        print(f"specter-agent: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
