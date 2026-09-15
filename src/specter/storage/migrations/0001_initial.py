"""Creates every table of the first database schema.

The tables are declared here as frozen copies, so later changes to the live table models never
change what this migration creates.
"""

from peewee import (
    SQL,
    BooleanField,
    CharField,
    CompositeKey,
    Database,
    FloatField,
    IntegerField,
    Model,
    TextField,
)
from playhouse.migrate import SchemaMigrator


class _Camera(Model):
    id = CharField(primary_key=True)
    owner_id = CharField(index=True)
    name = CharField()
    source_url = TextField()
    credentials_username = CharField(null=True)
    credentials_encrypted_password = TextField(null=True)
    transport = CharField()
    detection_classes_json = TextField()
    sampling_mode = CharField()
    target_fps = FloatField()
    minimum_fps = FloatField()
    is_motion_gating_enabled = BooleanField()
    is_enabled = BooleanField()
    desired_state = CharField()
    created_at = CharField()

    class Meta:
        table_name = "cameras"


class _Watchlist(Model):
    id = CharField(primary_key=True)
    owner_id = CharField(index=True)
    name = CharField()
    target_type = CharField()
    kind = CharField()
    match_threshold_ratio = FloatField()
    metadata_json = TextField()
    created_at = CharField()

    class Meta:
        table_name = "watchlists"


class _CameraWatchlist(Model):
    camera_id = CharField(constraints=[SQL("REFERENCES cameras (id) ON DELETE CASCADE")])
    watchlist_id = CharField(
        index=True, constraints=[SQL("REFERENCES watchlists (id) ON DELETE CASCADE")]
    )
    position = IntegerField()

    class Meta:
        table_name = "camera_watchlists"
        primary_key = CompositeKey("camera_id", "watchlist_id")


class _Target(Model):
    id = CharField(primary_key=True)
    watchlist_id = CharField(
        index=True, constraints=[SQL("REFERENCES watchlists (id) ON DELETE CASCADE")]
    )
    label = CharField()
    target_type = CharField()
    is_enabled = BooleanField()
    metadata_json = TextField()
    enrollment_batch_id = CharField(null=True, index=True)
    created_at = CharField()

    class Meta:
        table_name = "targets"


class _ReferenceImage(Model):
    id = CharField(primary_key=True)
    target_id = CharField(
        index=True, constraints=[SQL("REFERENCES targets (id) ON DELETE CASCADE")]
    )
    image_path = TextField()
    status = CharField()
    quality_json = TextField(null=True)
    rejection_reason = CharField(null=True)
    model_version = CharField(null=True)
    created_at = CharField()

    class Meta:
        table_name = "reference_images"


class _Zone(Model):
    id = CharField(primary_key=True)
    camera_id = CharField(
        index=True, constraints=[SQL("REFERENCES cameras (id) ON DELETE CASCADE")]
    )
    name = CharField()
    polygon_json = TextField()
    created_at = CharField()

    class Meta:
        table_name = "zones"


class _ZoneOccupancyRule(Model):
    id = CharField(primary_key=True)
    zone_id = CharField(index=True, constraints=[SQL("REFERENCES zones (id) ON DELETE CASCADE")])
    object_classes_json = TextField()
    minimum_dwell_seconds = FloatField()
    is_enabled = BooleanField()
    created_at = CharField()

    class Meta:
        table_name = "zone_occupancy_rules"


class _LineCrossingRule(Model):
    id = CharField(primary_key=True)
    camera_id = CharField(
        index=True, constraints=[SQL("REFERENCES cameras (id) ON DELETE CASCADE")]
    )
    line_start_x = FloatField()
    line_start_y = FloatField()
    line_end_x = FloatField()
    line_end_y = FloatField()
    direction = CharField()
    object_classes_json = TextField()
    is_enabled = BooleanField()
    created_at = CharField()

    class Meta:
        table_name = "line_crossing_rules"


class _IdentityMatchAlert(Model):
    id = CharField(primary_key=True)
    owner_id = CharField()
    camera_id = CharField()
    track_id = IntegerField()
    watchlist_id = CharField()
    target_id = CharField()
    similarity_ratio = FloatField()
    margin_ratio = FloatField()
    object_class = CharField()
    bounding_box_x = FloatField()
    bounding_box_y = FloatField()
    bounding_box_width = FloatField()
    bounding_box_height = FloatField()
    frame_captured_at = CharField()
    created_at = CharField()
    snapshot_path = TextField(null=True)
    disposition = CharField()
    is_acknowledged = BooleanField()
    note = TextField(null=True)

    class Meta:
        table_name = "identity_match_alerts"
        indexes = (
            (("owner_id", "created_at"), False),
            (("camera_id", "created_at"), False),
        )


class _RuleAlert(Model):
    id = CharField(primary_key=True)
    owner_id = CharField()
    camera_id = CharField()
    track_id = IntegerField()
    rule_id = CharField()
    rule_kind = CharField()
    zone_id = CharField(null=True)
    object_class = CharField()
    bounding_box_x = FloatField()
    bounding_box_y = FloatField()
    bounding_box_width = FloatField()
    bounding_box_height = FloatField()
    dwell_seconds = FloatField(null=True)
    crossing_direction = CharField(null=True)
    frame_captured_at = CharField()
    created_at = CharField()
    snapshot_path = TextField(null=True)
    disposition = CharField()
    is_acknowledged = BooleanField()
    note = TextField(null=True)

    class Meta:
        table_name = "rule_alerts"
        indexes = (
            (("owner_id", "created_at"), False),
            (("camera_id", "created_at"), False),
        )


INITIAL_TABLES = (
    _Camera,
    _Watchlist,
    _CameraWatchlist,
    _Target,
    _ReferenceImage,
    _Zone,
    _ZoneOccupancyRule,
    _LineCrossingRule,
    _IdentityMatchAlert,
    _RuleAlert,
)


def up(_migrator: SchemaMigrator, database: Database) -> None:
    """Creates the tables."""
    with database.bind_ctx(INITIAL_TABLES):
        database.create_tables(INITIAL_TABLES)
