import json
from time import time

from sqlalchemy import func, insert, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

from migrations import v001_workbench, v002_merges
from src.config.resources import engine
from src.config.settings import DEMO_PASSWORD
from src.constants.job_state import JobState, MergeState
from src.constants.roles import Role
from src.domain import merge as merge_domain
from src.domain import scenario
from src.domain.fixtures import MERGE_FIXTURE
from src.models.cases import cases
from src.models.merges import merges
from src.models.runs import runs
from src.models.users import users
from src.utils.passwords import hash_password
from src.utils.serial import content_digest


def insert_new(db, table, key, values):
    if db.dialect.name == "postgresql":
        stmt = pg_insert(table).values(**values).on_conflict_do_nothing(index_elements=[key])
    else:
        stmt = mysql_insert(table).values(**values).on_duplicate_key_update(**{key: table.c[key]})
    db.execute(stmt)


def setup_schema():
    with engine.begin() as db:
        v001_workbench.upgrade(db)
        v002_merges.upgrade(db)


def seed_merge(db):
    """幂等载入演示交班合单：两张已完工变更单和一张待取舍的合单。"""
    if db.scalar(select(func.count()).select_from(merges).where(merges.c.code == MERGE_FIXTURE["code"])):
        return
    run_ids = []
    for code in (MERGE_FIXTURE["day"], MERGE_FIXTURE["night"]):
        row = db.execute(select(cases.c.id, cases.c.payload).where(cases.c.code == code)).first()
        if row is None:
            return
        case_id, payload = row
        parameter = MERGE_FIXTURE["parameter"]
        result = scenario.run(payload, parameter, db)
        change = db.execute(insert(runs).values(
            case_id=case_id, status=JobState.DONE, parameter=parameter, actor="seed",
            created_at=int(time()), finished_at=int(time()),
            result=result, digest=content_digest(result),
        ))
        run_ids.append(change.inserted_primary_key[0])
    conflicts = merge_domain.conflict_keys(
        merge_domain.successful_ops(
            db.scalar(select(cases.c.payload).where(cases.c.code == MERGE_FIXTURE["day"])),
            MERGE_FIXTURE["parameter"]),
        merge_domain.successful_ops(
            db.scalar(select(cases.c.payload).where(cases.c.code == MERGE_FIXTURE["night"])),
            MERGE_FIXTURE["parameter"]),
    )
    db.execute(insert(merges).values(
        code=MERGE_FIXTURE["code"], title=MERGE_FIXTURE["title"],
        run_a_id=run_ids[0], run_b_id=run_ids[1], parameter=MERGE_FIXTURE["parameter"],
        status=MergeState.OPEN, conflicts=conflicts, choices_version=0,
        created_by="seed", created_at=int(time()),
    ))


def seed():
    with engine.begin() as db:
        existing = set(db.execute(select(users.c.username)).scalars())
        for role in Role:
            if role.value not in existing:
                insert_new(db, users, "username", {
                    "username": role.value, "role": role.value, "password_hash": hash_password(DEMO_PASSWORD),
                })
        for fixture in scenario.FIXTURES:
            insert_new(db, cases, "code", {**fixture, "created_by": "seed"})
        seed_merge(db)
        return {table.name: db.scalar(select(func.count()).select_from(table)) for table in (users, cases)}


if __name__ == "__main__":
    setup_schema()
    print(json.dumps(seed(), sort_keys=True))
