from sqlalchemy import JSON, BigInteger, Column, ForeignKey, Integer, String, Table
from .base import metadata


merge_runs = Table(
    "merge_runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("merge_id", ForeignKey("merges.id"), nullable=False, index=True),
    Column("status", String(16), nullable=False, index=True),
    Column("choices", JSON, nullable=False),
    Column("choices_version", Integer, nullable=False),
    Column("actor", String(48), nullable=False),
    Column("created_at", BigInteger, nullable=False),
    Column("finished_at", BigInteger, nullable=True),
    Column("result", JSON(none_as_null=True), nullable=True),
    Column("failure", String(320), nullable=True),
    Column("digest", String(64), nullable=True),
)
