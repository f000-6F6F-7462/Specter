"""Tracks the enrollment of each kind of embedding of a reference image separately.

A photo of a person from behind cannot enroll a face but still enrolls their appearance, so the
status, quality and model move from the image to one row per image and modality.
"""

from peewee import SQL, CharField, CompositeKey, Database, Model, TextField
from playhouse.migrate import SchemaMigrator, migrate


class _ImageEmbedding(Model):
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


def up(migrator: SchemaMigrator, database: Database) -> None:
    """Creates the image embeddings table from the images' single status and drops that status."""
    with database.atomic():
        with database.bind_ctx([_ImageEmbedding]):
            database.create_tables([_ImageEmbedding])
        # Every image enrolled so far was a face; people's images also still need their appearance.
        database.execute_sql(  # type: ignore[no-untyped-call]
            "INSERT INTO image_embeddings "
            "(reference_image_id, modality, status, quality_json, rejection_reason, model_version) "
            "SELECT id, 'face', status, quality_json, rejection_reason, model_version "
            "FROM reference_images"
        )
        database.execute_sql(  # type: ignore[no-untyped-call]
            "INSERT INTO image_embeddings (reference_image_id, modality, status) "
            "SELECT reference_images.id, 'appearance', 'pending' FROM reference_images "
            "JOIN targets ON targets.id = reference_images.target_id "
            "WHERE targets.target_type = 'person'"
        )
        migrate(  # type: ignore[no-untyped-call]
            migrator.drop_column("reference_images", "status"),
            migrator.drop_column("reference_images", "quality_json"),
            migrator.drop_column("reference_images", "rejection_reason"),
            migrator.drop_column("reference_images", "model_version"),
        )
