import re

from pydantic import BaseModel, Field, field_validator

from src.constants.job_state import JobState, MergeState


class MergeInput(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    run_a_id: int = Field(ge=1)
    run_b_id: int = Field(ge=1)

    @field_validator("title")
    @classmethod
    def check_title(cls, title):
        if len(title.strip()) < 2:
            raise ValueError("请输入至少两个字符的名称")
        return title.strip()


class ChoicesInput(BaseModel):
    choices: dict[str, str]

    @field_validator("choices")
    @classmethod
    def check_choices(cls, choices):
        for key, side in choices.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", key):
                raise ValueError("取舍履历号须为 1 至 24 位字母、数字、下划线或连字符")
            if side not in ("a", "b"):
                raise ValueError("每条履历只能取白班或夜班")
        return choices


class HandoverInput(BaseModel):
    run_id: int = Field(ge=1)
    note: str = Field(min_length=3, max_length=500)

    @field_validator("note")
    @classmethod
    def check_note(cls, note):
        if len(note.strip()) < 3:
            raise ValueError("交班备注至少需要三个字符")
        return note.strip()


class MergeEntry(BaseModel):
    id: int
    code: str
    title: str
    status: MergeState
    parameter: int
    run_a_id: int
    run_b_id: int
    conflicts: list[str]
    choices: dict | None
    choices_version: int
    choices_by: str | None
    choices_at: int | None
    created_by: str
    created_at: int


class MergeRunSummary(BaseModel):
    id: int
    merge_id: int
    status: JobState
    choices_version: int
    stale: bool
    fitted: bool | None
    actor: str
    created_at: int
    finished_at: int | None
    failure: str | None
    digest: str | None


class MergeDetail(MergeEntry):
    run_a: dict
    run_b: dict
    handover: dict | None
    runs: list[MergeRunSummary]


class MergeRunDetail(MergeRunSummary):
    merge_title: str
    choices: dict
    result: dict | None
