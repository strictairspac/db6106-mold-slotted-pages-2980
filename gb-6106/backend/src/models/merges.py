from sqlalchemy import JSON, BigInteger, Column, ForeignKey, Integer, String, Table
from .base import metadata


merges = Table(
    "merges", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("code", String(48), unique=True, nullable=False),
    Column("title", String(120), nullable=False),
    Column("run_a_id", ForeignKey("runs.id"), nullable=False),
    Column("run_b_id", ForeignKey("runs.id"), nullable=False),
    Column("parameter", Integer, nullable=False),
    Column("status", String(16), nullable=False),
    Column("conflicts", JSON, nullable=False),
    Column("choices", JSON(none_as_null=True), nullable=True),
    Column("choices_version", Integer, nullable=False),
    Column("choices_by", String(48), nullable=True),
    Column("choices_at", BigInteger, nullable=True),
    Column("created_by", String(48), nullable=False),
    Column("created_at", BigInteger, nullable=False),
)
