"""Query building blocks shared by the storage modules."""

from peewee import SQL, Expression, Model

# Rows saved in the same call share a timestamp, so the row id keeps their original order.
INSERTION_ORDER = SQL("rowid")


def delete_rows(record_type: type[Model], *conditions: Expression) -> int:
    """Deletes the table's rows that match every condition and returns how many were deleted."""
    # types-peewee declares delete() as a generic class-only method, which mypy rejects when it is
    # called on a concrete table class; typing the table as type[Model] keeps the call checked.
    return int(record_type.delete().where(*conditions).execute())
