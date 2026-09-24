"""Database tables, as peewee models.

Timestamps are stored as UTC ISO 8601 text and lists as JSON text. Relations are plain id columns
with a foreign key constraint, so reading a row never loads the related row by accident. The tables
know nothing about a database; ``storage.database.open_database`` binds them to one.
"""

from peewee import (
    SQL,
    BooleanField,
    CharField,
    CompositeKey,
    FloatField,
    IntegerField,
    Model,
    TextField,
)


class CameraRecord(Model):
    """A row of the ``cameras`` table."""

    id = CharField(primary_key=True)
    owner_id = CharField(index=True)
    name = CharField()
    source_url = TextField()
    credentials_username = CharField(null=True)
    credentials_encrypted_password = TextField(null=True)
    detection_classes_json = TextField()
    sampling_mode = CharField()
    target_fps = FloatField()
    minimum_fps = FloatField()
    is_motion_gating_enabled = BooleanField()
    is_enabled = BooleanField()
    desired_state = CharField()
    created_at = CharField()
    metadata_json = TextField(default="{}")

    class Meta:
        table_name = "cameras"


class WatchlistRecord(Model):
    """A row of the ``watchlists`` table."""

    id = CharField(primary_key=True)
    owner_id = CharField(index=True)
    name = CharField()
    target_type = CharField()
    kind = CharField()
    face_match_threshold_ratio = FloatField()
    appearance_match_threshold_ratio = FloatField()
    metadata_json = TextField()
    created_at = CharField()

    class Meta:
        table_name = "watchlists"


class CameraWatchlistRecord(Model):
    """A row of the ``camera_watchlists`` table: one watchlist that a camera uses."""

    camera_id = CharField(constraints=[SQL("REFERENCES cameras (id) ON DELETE CASCADE")])
    watchlist_id = CharField(
        index=True, constraints=[SQL("REFERENCES watchlists (id) ON DELETE CASCADE")]
    )
    position = IntegerField()

    class Meta:
        table_name = "camera_watchlists"
        primary_key = CompositeKey("camera_id", "watchlist_id")


class TargetRecord(Model):
    """A row of the ``targets`` table."""

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


class ReferenceImageRecord(Model):
    """A row of the ``reference_images`` table."""

    id = CharField(primary_key=True)
    target_id = CharField(
        index=True, constraints=[SQL("REFERENCES targets (id) ON DELETE CASCADE")]
    )
    image_path = TextField()
    created_at = CharField()

    class Meta:
        table_name = "reference_images"


class ImageEmbeddingRecord(Model):
    """A row of the ``image_embeddings`` table: one kind of embedding of a reference image."""

    reference_image_id = CharField(
        constraints=[SQL("REFERENCES reference_images (id) ON DELETE CASCADE")]
    )
    modality = CharField()
    status = CharField(index=True)
    quality_json = TextField(null=True)
    rejection_reason = CharField(null=True)
    model_version = CharField(null=True)

    class Meta:
        table_name = "image_embeddings"
        primary_key = CompositeKey("reference_image_id", "modality")


class ZoneRecord(Model):
    """A row of the ``zones`` table."""

    id = CharField(primary_key=True)
    camera_id = CharField(
        index=True, constraints=[SQL("REFERENCES cameras (id) ON DELETE CASCADE")]
    )
    name = CharField()
    polygon_json = TextField()
    created_at = CharField()

    class Meta:
        table_name = "zones"


class ZoneOccupancyRuleRecord(Model):
    """A row of the ``zone_occupancy_rules`` table."""

    id = CharField(primary_key=True)
    zone_id = CharField(index=True, constraints=[SQL("REFERENCES zones (id) ON DELETE CASCADE")])
    object_classes_json = TextField()
    minimum_dwell_seconds = FloatField()
    is_enabled = BooleanField()
    created_at = CharField()

    class Meta:
        table_name = "zone_occupancy_rules"


class LineCrossingRuleRecord(Model):
    """A row of the ``line_crossing_rules`` table."""

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


# Alerts keep plain ids without foreign keys, so alert history survives deleting a camera,
# watchlist, target or rule.
class IdentityMatchAlertRecord(Model):
    """A row of the ``identity_match_alerts`` table."""

    id = CharField(primary_key=True)
    owner_id = CharField()
    camera_id = CharField()
    track_id = IntegerField()
    watchlist_id = CharField()
    target_id = CharField()
    modality = CharField()
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


class RuleAlertRecord(Model):
    """A row of the ``rule_alerts`` table."""

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


RECORD_TYPES: tuple[type[Model], ...] = (
    CameraRecord,
    WatchlistRecord,
    CameraWatchlistRecord,
    TargetRecord,
    ReferenceImageRecord,
    ImageEmbeddingRecord,
    ZoneRecord,
    ZoneOccupancyRuleRecord,
    LineCrossingRuleRecord,
    IdentityMatchAlertRecord,
    RuleAlertRecord,
)
