from time import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import insert, select, update

from src.constants.job_state import JobState, MergeState
from src.domain import merge as domain
from src.models.cases import cases
from src.models.handovers import handovers
from src.models.merge_runs import merge_runs
from src.models.merges import merges
from src.models.runs import runs
from . import audit

RUN_COLUMNS = (
    merge_runs.c.id, merge_runs.c.merge_id, merge_runs.c.status,
    merge_runs.c.choices_version, merge_runs.c.actor, merge_runs.c.created_at,
    merge_runs.c.finished_at, merge_runs.c.failure, merge_runs.c.digest,
    merge_runs.c.result,
)


def _source(db, run_id):
    row = db.execute(
        select(runs, cases.c.title.label("case_title"), cases.c.payload.label("case_payload"))
        .join(cases, cases.c.id == runs.c.case_id).where(runs.c.id == run_id)
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "变更单批次不存在")
    return dict(row)


def _lock(db, merge_id):
    row = db.execute(
        select(merges).where(merges.c.id == merge_id).with_for_update()
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "交班合单不存在")
    return dict(row)


def get(db, merge_id):
    row = db.execute(select(merges).where(merges.c.id == merge_id)).mappings().first()
    if row is None:
        raise HTTPException(404, "交班合单不存在")
    return dict(row)


def list_entries(db):
    return [dict(row) for row in db.execute(select(merges).order_by(merges.c.id.desc()).limit(100)).mappings()]


def _run_entry(row, choices_version):
    entry = dict(row)
    result = entry.pop("result", None)
    entry["fitted"] = result.get("fitted") if result else None
    entry["stale"] = entry["choices_version"] != choices_version
    return entry


def detail(db, merge_id):
    merge = get(db, merge_id)
    for side in ("a", "b"):
        source = _source(db, merge[f"run_{side}_id"])
        source.pop("case_payload", None)
        merge[f"run_{side}"] = source
    handover = db.execute(select(handovers).where(handovers.c.merge_id == merge_id)).mappings().first()
    merge["handover"] = dict(handover) if handover else None
    rows = db.execute(
        select(*RUN_COLUMNS).where(merge_runs.c.merge_id == merge_id).order_by(merge_runs.c.id.desc())
    ).mappings()
    merge["runs"] = [_run_entry(row, merge["choices_version"]) for row in rows]
    return merge


def create(db, data, actor):
    if data.run_a_id == data.run_b_id:
        raise HTTPException(422, "白班和夜班不能是同一张变更单")
    run_a = _source(db, data.run_a_id)
    run_b = _source(db, data.run_b_id)
    if run_a["status"] != JobState.DONE or run_b["status"] != JobState.DONE:
        raise HTTPException(409, "两张变更单都已完工才能合单")
    if run_a["parameter"] != run_b["parameter"]:
        raise HTTPException(409, "两张变更单页容量不同，不能合单")
    parameter = run_a["parameter"]
    conflicts = domain.conflict_keys(
        domain.successful_ops(run_a["case_payload"], parameter),
        domain.successful_ops(run_b["case_payload"], parameter),
    )
    change = db.execute(insert(merges).values(
        code=f"MERGE-{uuid4().hex[:12].upper()}", title=data.title,
        run_a_id=data.run_a_id, run_b_id=data.run_b_id, parameter=parameter,
        status=MergeState.OPEN, conflicts=conflicts, choices_version=0,
        created_by=actor, created_at=int(time()),
    ))
    merge_id = change.inserted_primary_key[0]
    audit.record(db, "create_merge", actor, "merge", merge_id)
    return detail(db, merge_id)


def submit_choices(db, merge_id, data, actor):
    # 行锁把并发的两次取舍串行化，后确认的一方覆盖前者，双方都不会报错。
    merge = _lock(db, merge_id)
    if merge["status"] != MergeState.OPEN:
        raise HTTPException(409, "交班合单已完工，取舍不能再改")
    conflicts = set(merge["conflicts"])
    if set(data.choices) != conflicts:
        parts = []
        if missing := sorted(conflicts - set(data.choices)):
            parts.append(f"缺少：{'、'.join(missing)}")
        if extra := sorted(set(data.choices) - conflicts):
            parts.append(f"多余：{'、'.join(extra)}")
        raise HTTPException(422, f"取舍必须覆盖全部冲突履历（{'；'.join(parts)}）")
    db.execute(update(merges).where(merges.c.id == merge_id).values(
        choices=dict(data.choices), choices_version=merge["choices_version"] + 1,
        choices_by=actor, choices_at=int(time()),
    ))
    audit.record(db, "choices", actor, "merge", merge_id)
    return detail(db, merge_id)


def enqueue(db, merge_id, actor):
    merge = _lock(db, merge_id)
    if merge["status"] != MergeState.OPEN:
        raise HTTPException(409, "交班合单已完工，不能再提交执行")
    choices = merge["choices"] or {}
    missing = [key for key in merge["conflicts"] if key not in choices]
    if missing:
        raise HTTPException(409, f"还有履历未取舍：{'、'.join(missing)}")
    change = db.execute(insert(merge_runs).values(
        merge_id=merge_id, status=JobState.QUEUED, choices=choices,
        choices_version=merge["choices_version"], actor=actor, created_at=int(time()),
    ))
    run_id = change.inserted_primary_key[0]
    audit.record(db, "enqueue", actor, "merge_run", run_id)
    return run_detail(db, merge_id, run_id)


def run_detail(db, merge_id, run_id):
    row = db.execute(
        select(merge_runs).where(merge_runs.c.id == run_id, merge_runs.c.merge_id == merge_id)
    ).mappings().first()
    if row is None:
        raise HTTPException(404, "合单批次不存在")
    merge = get(db, merge_id)
    entry = _run_entry(row, merge["choices_version"])
    entry["result"] = row["result"]
    entry["choices"] = row["choices"]
    entry["merge_title"] = merge["title"]
    return entry


def handover(db, merge_id, data, actor):
    merge = _lock(db, merge_id)
    if merge["status"] != MergeState.OPEN:
        raise HTTPException(409, "该交班合单已经交班")
    run = db.execute(
        select(merge_runs).where(merge_runs.c.id == data.run_id, merge_runs.c.merge_id == merge_id)
    ).mappings().first()
    if run is None:
        raise HTTPException(404, "合单批次不存在")
    if run["status"] != JobState.DONE:
        raise HTTPException(409, "合单执行成功后才能交班")
    if not (run["result"] or {}).get("fitted"):
        raise HTTPException(409, "装不进原容量的合单不能交班，请调整取舍后重跑")
    if run["choices_version"] != merge["choices_version"]:
        raise HTTPException(409, "取舍已更新，旧取舍跑完的结果不能交班，请用最新取舍重跑")
    db.execute(insert(handovers).values(
        merge_id=merge_id, run_id=data.run_id, actor=actor, note=data.note, created_at=int(time()),
    ))
    db.execute(update(merges).where(merges.c.id == merge_id).values(status=MergeState.HANDED))
    audit.record(db, "handover", actor, "merge", merge_id)
    return detail(db, merge_id)


def export(db, merge_id, run_id):
    merge = get(db, merge_id)
    run = run_detail(db, merge_id, run_id)
    if run["status"] != JobState.DONE:
        raise HTTPException(409, "没有可导出的合单结果")
    handover = db.execute(select(handovers).where(handovers.c.merge_id == merge_id)).mappings().first()
    return {
        "merge": {key: merge[key] for key in (
            "id", "code", "title", "status", "parameter", "run_a_id", "run_b_id",
            "conflicts", "choices_version", "choices_by", "choices_at",
        )},
        "handover": dict(handover) if handover else None,
        "run": run,
    }
