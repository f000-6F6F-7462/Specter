"""``specter-enroll`` — consume ``specter:jobs:enroll`` and embed each reference image.

One job == one ``ReferenceImage``. Every job is acked after handling (success or logged
failure); the ``EnrollmentStatusMessage`` published by ``run_enrollment`` is the record
of what happened.
"""

import asyncio
import logging
import os

from specter.application.enrollment import EnrollmentDeps, run_enrollment
from specter.application.ports import Delivery
from specter.contracts import EnrollJobMessage
from specter.contracts.streams import ENROLL_GROUP, JOBS_ENROLL

log = logging.getLogger(__name__)


async def consume_forever(deps: EnrollmentDeps, *, consumer: str) -> None:
    async for delivery in deps.bus.consume(JOBS_ENROLL, group=ENROLL_GROUP, consumer=consumer):
        await _handle(deps, delivery)


async def _handle(deps: EnrollmentDeps, delivery: Delivery) -> None:
    message = delivery.message
    try:
        if isinstance(message, EnrollJobMessage):
            status = await run_enrollment(deps, message)
            log.info(
                "enrollment %s",
                status.status,
                extra={"target_id": message.target_id, "image_id": message.image_id},
            )
        else:
            log.warning("unexpected message on %s: %s", JOBS_ENROLL, type(message).__name__)
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        # A bad job must not stall the stream; the failure is logged and the job acked.
        log.exception("enrollment job failed", extra={"event_id": message.event_id})
    finally:
        await delivery.ack()


def main() -> None:  # pragma: no cover - process entrypoint
    from specter.core.di import build_container

    container = build_container()
    deps = EnrollmentDeps(
        uow_factory=container.uow_factory,
        blob=container.blob,
        faces=container.faces,
        vectors=container.vectors,
        bus=container.bus,
        clock=container.clock,
    )
    asyncio.run(consume_forever(deps, consumer=f"enroll-{os.getpid()}"))
