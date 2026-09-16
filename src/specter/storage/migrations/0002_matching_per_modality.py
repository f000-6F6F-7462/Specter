"""Separates face matching from appearance matching in watchlists and match alerts.

Face and appearance models score similarity on different scales, so each watchlist gets a threshold
per modality, and each match alert records which modality confirmed it.
"""

from peewee import CharField, Database, FloatField
from playhouse.migrate import SchemaMigrator, migrate

# Every match alert written before this migration came from a face.
EXISTING_ALERTS_MODALITY = "face"
# The single threshold was tuned for neither model, so existing watchlists take the new defaults.
DEFAULT_FACE_MATCH_THRESHOLD_RATIO = 0.45
DEFAULT_APPEARANCE_MATCH_THRESHOLD_RATIO = 0.75


def up(migrator: SchemaMigrator, database: Database) -> None:
    """Adds the modality columns and removes the single watchlist threshold."""
    with database.atomic():
        migrate(  # type: ignore[no-untyped-call]
            migrator.add_column(
                "identity_match_alerts",
                "modality",
                CharField(default=EXISTING_ALERTS_MODALITY),
            ),
            migrator.add_column(
                "watchlists",
                "face_match_threshold_ratio",
                FloatField(default=DEFAULT_FACE_MATCH_THRESHOLD_RATIO),
            ),
            migrator.add_column(
                "watchlists",
                "appearance_match_threshold_ratio",
                FloatField(default=DEFAULT_APPEARANCE_MATCH_THRESHOLD_RATIO),
            ),
            migrator.drop_column("watchlists", "match_threshold_ratio"),
        )
