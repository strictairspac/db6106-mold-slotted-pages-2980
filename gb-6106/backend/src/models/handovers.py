from sqlalchemy import BigInteger, Column, ForeignKey, String, Table
from .base import metadata


handovers = Table(
    "handovers", metadata,
    Column("merge_id", ForeignKey("merges.id"), primary_key=True),
    Column("run_id", ForeignKey("merge_runs.id"), nullable=False),
    Column("actor", String(48), nullable=False),
    Column("note", String(500), nullable=False),
    Column("created_at", BigInteger, nullable=False),
)
