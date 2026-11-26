from src.models import handovers, merge_runs, merges  # Register the tables.
from src.models.base import metadata


def upgrade(connection):
    # Version 2 adds the handover merge tables, still additive and declarative.
    metadata.create_all(connection)
