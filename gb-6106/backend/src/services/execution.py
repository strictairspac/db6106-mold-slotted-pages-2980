import logging
from time import time

from sqlalchemy import select, update

from src.config.resources import engine
from src.constants.job_state import JobState
from src.domain import scenario
from src.domain.merge import run_merge
from src.models.cases import cases
from src.models.merge_runs import merge_runs
from src.models.merges import merges
from src.models.runs import runs
from src.services.audit import record
from src.utils.serial import content_digest

logger = logging.getLogger(__name__)

QUEUED_RUNS = {"run": runs, "merge_run": merge_runs}


def service_one():
    with engine.begin() as db:
        statement = (
            select(runs).where(runs.c.status == JobState.QUEUED).order_by(runs.c.id)
            .limit(1).with_for_update(skip_locked=True)
        )
        task = db.execute(statement).mappings().first()
        if task is None:
            return False
        payload = db.scalar(select(cases.c.payload).where(cases.c.id == task["case_id"]))
        try:
            result = scenario.run(payload, task["parameter"], db)
            output = {"status": JobState.DONE, "result": result, "digest": content_digest(result)}
        except Exception as exc:
            # Let the failed database transaction roll back before persisting the error.
            raise JobFailure(task["id"], exc) from exc
        finish(db, task["id"], output)
        return True


def service_merge_one():
    with engine.begin() as db:
        statement = (
            select(merge_runs).where(merge_runs.c.status == JobState.QUEUED)
            .order_by(merge_runs.c.id).limit(1).with_for_update(skip_locked=True)
        )
        task = db.execute(statement).mappings().first()
        if task is None:
            return False
        merge = db.execute(select(merges).where(merges.c.id == task["merge_id"])).mappings().first()
        payload_a = _source_payload(db, merge["run_a_id"])
        payload_b = _source_payload(db, merge["run_b_id"])
        try:
            result = run_merge(payload_a, payload_b, merge["parameter"], task["choices"])
            result = {
                **result,
                "sources": {"白班": merge["run_a_id"], "夜班": merge["run_b_id"]},
                "choices": task["choices"],
                "choices_version": task["choices_version"],
            }
            output = {"status": JobState.DONE, "result": result, "digest": content_digest(result)}
        except Exception as exc:
            # Let the failed database transaction roll back before persisting the error.
            raise JobFailure(task["id"], exc, entity="merge_run") from exc
        finish(db, task["id"], output, entity="merge_run")
        return True


def _source_payload(db, run_id):
    case_id = db.scalar(select(runs.c.case_id).where(runs.c.id == run_id))
    return db.scalar(select(cases.c.payload).where(cases.c.id == case_id))


class JobFailure(Exception):
    def __init__(self, run_id, cause, entity="run"):
        super().__init__(str(cause))
        self.run_id = run_id
        self.cause = cause
        self.entity = entity


def fail(job_failure):
    table = QUEUED_RUNS[job_failure.entity]
    with engine.begin() as db:
        run_id = job_failure.run_id
        statement = select(table.c.status).where(table.c.id == run_id).with_for_update()
        if db.scalar(statement) != JobState.QUEUED:
            return
        cause = job_failure.cause
        if isinstance(cause, (ValueError, TypeError, KeyError, OverflowError)):
            reason = str(cause) or "输入资料或批次参数不符合要求"
        else:
            reason = "处理失败，请检查 worker 日志"
        finish(db, run_id, {"status": JobState.FAILED, "failure": reason[:320]}, entity=job_failure.entity)


def finish(db, run_id, output, entity="run"):
    table = QUEUED_RUNS[entity]
    db.execute(update(table).where(table.c.id == run_id).values(**output, finished_at=int(time())))
    record(db, str(output["status"]), "worker", entity, run_id)
