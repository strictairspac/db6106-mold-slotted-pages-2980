from enum import StrEnum


class JobState(StrEnum):
    QUEUED = "queued"
    DONE = "done"
    FAILED = "failed"


class MergeState(StrEnum):
    OPEN = "open"
    HANDED = "handed"
