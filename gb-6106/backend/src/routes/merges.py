from fastapi import APIRouter, Depends, Response

from src.config.resources import engine
from src.controllers.access import identity, operator, reviewer, shift_pair
from src.controllers.workbench import new_choices, new_handover, new_merge, new_merge_run
from src.schemas.merges import (
    ChoicesInput, HandoverInput, MergeDetail, MergeEntry, MergeInput, MergeRunDetail,
)
from src.services import merges
from src.utils.serial import canonical_json

router = APIRouter(prefix="/merges", tags=["Handover"])


@router.get("", response_model=list[MergeEntry], dependencies=[Depends(identity)])
def list_merges():
    with engine.connect() as db:
        return merges.list_entries(db)


@router.post("", response_model=MergeDetail, status_code=201)
def register(form: MergeInput, user=Depends(operator)):
    return new_merge(form, user)


@router.get("/{merge_id}", response_model=MergeDetail, dependencies=[Depends(identity)])
def merge_detail(merge_id: int):
    with engine.connect() as db:
        return merges.detail(db, merge_id)


@router.put("/{merge_id}/choices", response_model=MergeDetail)
def decide(merge_id: int, form: ChoicesInput, user=Depends(shift_pair)):
    return new_choices(merge_id, form, user)


@router.post("/{merge_id}/runs", response_model=MergeRunDetail, status_code=202)
def schedule(merge_id: int, user=Depends(operator)):
    return new_merge_run(merge_id, user)


@router.get("/{merge_id}/runs/{run_id}", response_model=MergeRunDetail, dependencies=[Depends(identity)])
def merge_run_detail(merge_id: int, run_id: int):
    with engine.connect() as db:
        return merges.run_detail(db, merge_id, run_id)


@router.get("/{merge_id}/runs/{run_id}/export", dependencies=[Depends(identity)])
def export(merge_id: int, run_id: int):
    with engine.connect() as db:
        payload = merges.export(db, merge_id, run_id)
    return Response(canonical_json(payload), media_type="application/json", headers={
        "Content-Disposition": f"attachment; filename=handover-{merge_id}-{run_id}.json",
    })


@router.post("/{merge_id}/handover", response_model=MergeDetail, status_code=201)
def hand_over(merge_id: int, form: HandoverInput, user=Depends(reviewer)):
    return new_handover(merge_id, form, user)
