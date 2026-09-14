"""``specter-ingest`` — the pipeline supervisor.

Polls the stream table and reconciles it against the set of running pipeline tasks: it
starts a ``run_stream`` task for every enabled stream whose desired state is *running*,
stops the ones that no longer qualify, restarts the ones whose config changed, and
re-launches crashed ones after a short backoff. One process; one ``asyncio`` task per
stream; the detector / tracker / embedders are shared across them — the real detector
and embedders coalesce every stream's calls into shared forward passes via
``BatchedDetector``/``BatchedEmbedder`` (see ``infrastructure.ml.inference_service``).
"""

import asyncio
import contextlib
import logging
import signal
from dataclasses import dataclass, field

from specter.application.pipeline import PipelineDeps, run_stream
from specter.application.ports import UnitOfWorkFactory
from specter.domain.streams import StreamConfig

log = logging.getLogger(__name__)

_POLL_S = 5.0
_RESTART_BACKOFF_S = 5.0
_STOP_GRACE_S = 10.0


@dataclass(slots=True)
class _Run:
    config: StreamConfig
    task: asyncio.Task[object]
    stop: asyncio.Event


@dataclass(slots=True)
class _Supervisor:
    deps: PipelineDeps
    uow_factory: UnitOfWorkFactory
    poll_s: float = _POLL_S
    backoff_s: float = _RESTART_BACKOFF_S
    running: dict[str, _Run] = field(default_factory=dict)
    cooldown: dict[str, float] = field(default_factory=dict)
    reconnects: dict[str, int] = field(default_factory=dict)
    """Crash count since the stream was last cleanly stopped/started — reported through
    StreamHealth.reconnect_count (run_stream itself has no memory across restarts)."""

    async def run(self, *, stop: asyncio.Event) -> None:
        try:
            while not stop.is_set():
                await self._reconcile(await self._wanted())
                await _wait(stop, self.poll_s)
        finally:
            await self._stop_all()

    async def _wanted(self) -> dict[str, StreamConfig]:
        async with self.uow_factory() as uow:
            streams = await uow.streams.list_enabled()
        return {s.id: s for s in streams if s.should_run}

    async def _reconcile(self, wanted: dict[str, StreamConfig]) -> None:
        for stream_id, run in list(self.running.items()):
            if run.task.done():
                self._reap(stream_id, run)
            elif stream_id not in wanted or wanted[stream_id] != run.config:
                await self._stop_one(stream_id)

        # pylint mis-reads the `...`-bodied Clock protocol method as returning None.
        now = self.deps.clock.now()  # pylint: disable=assignment-from-no-return
        for stream_id, config in wanted.items():
            if stream_id in self.running or self.cooldown.get(stream_id, 0.0) > now:
                continue
            self._start(config)

    def _start(self, config: StreamConfig) -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(
            run_stream(
                self.deps, config, stop=stop, reconnect_count=self.reconnects.get(config.id, 0)
            ),
            name=f"stream:{config.id}",
        )
        self.running[config.id] = _Run(config=config, task=task, stop=stop)
        log.info("stream %s started", config.id)

    def _reap(self, stream_id: str, run: _Run) -> None:
        del self.running[stream_id]
        exc = run.task.exception() if not run.task.cancelled() else None
        if exc is not None:
            self.reconnects[stream_id] = self.reconnects.get(stream_id, 0) + 1
            self.cooldown[stream_id] = self.deps.clock.now() + self.backoff_s
            log.warning("stream %s crashed (%r); backing off %.0fs", stream_id, exc, self.backoff_s)
        else:
            self.cooldown.pop(stream_id, None)
            self.reconnects.pop(stream_id, None)
            log.info("stream %s finished", stream_id)

    async def _stop_one(self, stream_id: str) -> None:
        run = self.running.pop(stream_id, None)
        if run is None:
            return
        run.stop.set()
        _done, pending = await asyncio.wait({run.task}, timeout=_STOP_GRACE_S)
        if pending:
            run.task.cancel()
        await asyncio.gather(run.task, return_exceptions=True)
        self.reconnects.pop(stream_id, None)  # a deliberate stop clears the crash count
        log.info("stream %s stopped", stream_id)

    async def _stop_all(self) -> None:
        await asyncio.gather(
            *(self._stop_one(stream_id) for stream_id in list(self.running)),
            return_exceptions=True,
        )


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


async def supervise(
    deps: PipelineDeps,
    uow_factory: UnitOfWorkFactory,
    *,
    stop: asyncio.Event,
    poll_s: float = _POLL_S,
    backoff_s: float = _RESTART_BACKOFF_S,
) -> None:
    await _Supervisor(deps=deps, uow_factory=uow_factory, poll_s=poll_s, backoff_s=backoff_s).run(
        stop=stop
    )


def main() -> None:  # pragma: no cover - process entrypoint
    from specter.core.di import build_container

    container = build_container()
    deps = PipelineDeps(
        uow_factory=container.uow_factory,
        frame_source_factory=container.frame_source_factory,
        detector=container.detector,
        tracker=container.tracker,
        embedders=container.embedders,
        vectors=container.vectors,
        blob=container.blob,
        bus=container.bus,
        health=container.health,
        clock=container.clock,
        codec=container.codec,
        tuning=container.pipeline_tuning,
    )

    async def _run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        # Starts/stops each batched adapter's MicroBatcher loop around the whole run.
        async with contextlib.AsyncExitStack() as stack:
            for adapter in container.lifecycle:
                await stack.enter_async_context(adapter)
            await supervise(deps, container.uow_factory, stop=stop)
        await container.engine.dispose()

    asyncio.run(_run())


if __name__ == "__main__":  # pragma: no cover
    main()
