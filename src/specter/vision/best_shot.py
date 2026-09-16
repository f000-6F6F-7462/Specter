"""Chooses when a track's face or appearance is worth embedding again."""

from dataclasses import dataclass, replace

# Consecutive frames look almost the same, so a track is sampled at most this often.
DEFAULT_SAMPLE_INTERVAL_SECONDS = 0.4
# Without a better shot, a track is still embedded this often so that identity matching gets the
# repeated samples it confirms a match from.
DEFAULT_REFRESH_INTERVAL_SECONDS = 1.5
# A shot must beat the best one so far by this much to count as better, which ignores noise.
DEFAULT_IMPROVEMENT_RATIO = 0.1
# Enough samples to confirm a match many times over; a track that stays unmatched after that is
# not going to match.
DEFAULT_MAXIMUM_EMBEDDING_COUNT = 20


@dataclass(frozen=True, slots=True)
class _TrackShots:
    best_quality_score_ratio: float = 0.0
    last_sampled_at_seconds: float | None = None
    last_embedded_at_seconds: float | None = None
    embedding_count: int = 0
    is_finished: bool = False


class BestShotSelector:
    """Decides, for one kind of embedding, which tracks to sample and how good a shot must be.

    A shot is embedded when it is clearly better than the track's best so far, or when the track
    has gone without an embedding for the refresh interval. Tracks stop being sampled once they
    are finished, such as after a confirmed match, or after the maximum number of embeddings.
    """

    def __init__(
        self,
        *,
        sample_interval_seconds: float = DEFAULT_SAMPLE_INTERVAL_SECONDS,
        refresh_interval_seconds: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
        improvement_ratio: float = DEFAULT_IMPROVEMENT_RATIO,
        maximum_embedding_count: int = DEFAULT_MAXIMUM_EMBEDDING_COUNT,
    ) -> None:
        self._sample_interval_seconds = sample_interval_seconds
        self._refresh_interval_seconds = refresh_interval_seconds
        self._improvement_ratio = improvement_ratio
        self._maximum_embedding_count = maximum_embedding_count
        self._shots_by_track_id: dict[int, _TrackShots] = {}

    def minimum_quality_score(self, track_id: int, now_seconds: float) -> float | None:
        """Returns the score a new shot of the track must beat, or None to skip the track now."""
        shots = self._shots_by_track_id.get(track_id, _TrackShots())
        if shots.is_finished or shots.embedding_count >= self._maximum_embedding_count:
            return None
        if (
            shots.last_sampled_at_seconds is not None
            and now_seconds - shots.last_sampled_at_seconds < self._sample_interval_seconds
        ):
            return None
        if (
            shots.last_embedded_at_seconds is None
            or now_seconds - shots.last_embedded_at_seconds >= self._refresh_interval_seconds
        ):
            return 0.0
        return min(shots.best_quality_score_ratio * (1.0 + self._improvement_ratio), 1.0)

    def record_sample(
        self,
        track_id: int,
        quality_score_ratio: float | None,
        *,
        was_embedded: bool,
        now_seconds: float,
    ) -> None:
        """Records that the track was sampled, with the shot's score when a shot was found."""
        shots = self._shots_by_track_id.get(track_id, _TrackShots())
        shots = replace(shots, last_sampled_at_seconds=now_seconds)
        if was_embedded and quality_score_ratio is not None:
            shots = replace(
                shots,
                best_quality_score_ratio=max(shots.best_quality_score_ratio, quality_score_ratio),
                last_embedded_at_seconds=now_seconds,
                embedding_count=shots.embedding_count + 1,
            )
        self._shots_by_track_id[track_id] = shots

    def finish(self, track_id: int) -> None:
        """Stops sampling the track."""
        shots = self._shots_by_track_id.get(track_id, _TrackShots())
        self._shots_by_track_id[track_id] = replace(shots, is_finished=True)

    def forget_tracks(self, track_ids: frozenset[int]) -> None:
        """Drops the shots of tracks that ended."""
        for track_id in track_ids:
            self._shots_by_track_id.pop(track_id, None)
