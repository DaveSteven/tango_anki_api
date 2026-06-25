from typing import Literal

from pydantic import BaseModel, Field


class ReviewPayload(BaseModel):
    state: Literal["new", "learning", "review", "relearning"]
    due: int
    interval: int = 0
    ease: float = 2.5
    reps: int = 0
    lapses: int = 0
    step: int = 0
    lastReviewed: int | None = None


class DailyProgressPayload(BaseModel):
    date: str
    newCompleted: int = Field(default=0, ge=0)
    reviewCompleted: int = Field(default=0, ge=0)
    completedIds: list[str] = Field(default_factory=list)


class SettingsPayload(BaseModel):
    newPerDay: int = Field(default=20, ge=0, le=999)


class MigrationPayload(BaseModel):
    reviews: dict[str, ReviewPayload] = Field(default_factory=dict)
    daily: DailyProgressPayload | None = None
    settings: SettingsPayload | None = None


class StudyStateResponse(BaseModel):
    reviews: dict[str, ReviewPayload]
    daily: DailyProgressPayload | None
    settings: SettingsPayload
