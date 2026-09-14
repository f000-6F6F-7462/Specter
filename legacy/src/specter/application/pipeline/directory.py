"""The resolved matching context for one stream.

``VectorIndex.search`` returns bare ``(target_id, similarity)`` candidates. To turn one
into a ``MatchEvent`` the pipeline needs the target's label/type and its watchlist's
name/kind/threshold. Resolving that per frame would hammer the database, so it is
snapshotted here when the stream starts and rebuilt on an interval.

A target is eligible if its watchlist belongs to the stream's owner and the target is
enabled with at least one embedded reference image.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from specter.application.ports import UnitOfWorkFactory
from specter.domain.matching import NofMPolicy
from specter.domain.streams import StreamConfig


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    target_id: str
    label: str
    target_type: str
    watchlist_id: str
    watchlist_name: str
    watchlist_kind: str
    threshold: float


@dataclass(frozen=True, slots=True)
class StreamDirectory:
    owner_id: str
    watchlist_ids: tuple[str, ...]
    targets: Mapping[str, ResolvedTarget]

    @property
    def has_targets(self) -> bool:
        return bool(self.targets)

    def resolve(self, target_id: str) -> ResolvedTarget | None:
        return self.targets.get(target_id)

    def policies(
        self, *, need: int, window: int, ema_alpha: float, cooldown_s: float
    ) -> dict[str, NofMPolicy]:
        """One firing policy per watchlist, carrying that watchlist's threshold."""
        thresholds = {t.watchlist_id: t.threshold for t in self.targets.values()}
        return {
            watchlist_id: NofMPolicy(
                threshold=threshold,
                need=need,
                window=window,
                ema_alpha=ema_alpha,
                cooldown_s=cooldown_s,
            )
            for watchlist_id, threshold in thresholds.items()
        }

    @classmethod
    async def load(cls, uow_factory: UnitOfWorkFactory, stream: StreamConfig) -> "StreamDirectory":
        resolved: dict[str, ResolvedTarget] = {}
        seen_watchlists: list[str] = []
        async with uow_factory() as uow:
            for watchlist_id in stream.watchlist_ids:
                watchlist = await uow.watchlists.get(watchlist_id)
                if watchlist is None or watchlist.owner_id != stream.owner_id:
                    continue
                seen_watchlists.append(watchlist_id)
                for target in await uow.targets.list_for_watchlist(watchlist_id):
                    if not target.enabled or not target.embedded_images:
                        continue
                    resolved[target.id] = ResolvedTarget(
                        target_id=target.id,
                        label=target.label,
                        target_type=target.type.value,
                        watchlist_id=watchlist.id,
                        watchlist_name=watchlist.name,
                        watchlist_kind=watchlist.kind.value,
                        threshold=watchlist.match_threshold,
                    )
        return cls(
            owner_id=stream.owner_id,
            watchlist_ids=tuple(seen_watchlists),
            targets=resolved,
        )
