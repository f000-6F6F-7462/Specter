"""Composition root.

The one place concrete adapters are chosen and wired.
"""

from dataclasses import dataclass

from specter.platform.clock import Clock, SystemClock
from specter.platform.logging import configure_logging
from specter.platform.settings import Settings


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    clock: Clock


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or Settings()
    configure_logging(settings.log.level, json_output=settings.log.json_output)
    return Container(settings=settings, clock=SystemClock())
