from redis.exceptions import RedisError
from src.config.resources import cache, engine
from src.services import cases, merges, reviews, runs


def new_case(data, user):
    with engine.begin() as db:
        return cases.create(db, data, user["username"])


def new_run(case_id, data, user):
    with engine.begin() as db:
        run = runs.enqueue(db, case_id, data.parameter, user["username"])
    wake(run)
    return run


def new_review(run_id, data, user):
    with engine.begin() as db:
        return reviews.sign(db, run_id, data, user["username"])


def new_merge(data, user):
    with engine.begin() as db:
        return merges.create(db, data, user["username"])


def new_choices(merge_id, data, user):
    with engine.begin() as db:
        return merges.submit_choices(db, merge_id, data, user["username"])


def new_merge_run(merge_id, user):
    with engine.begin() as db:
        run = merges.enqueue(db, merge_id, user["username"])
    wake(run)
    return run


def new_handover(merge_id, data, user):
    with engine.begin() as db:
        return merges.handover(db, merge_id, data, user["username"])


def wake(run):
    try:
        cache.lpush("workbench:wake", str(run["id"]))
        cache.ltrim("workbench:wake", 0, 99)
    except RedisError:
        # The database holds the durable queue. The worker also polls it.
        pass
