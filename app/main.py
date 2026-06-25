import os
from collections.abc import Generator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .models import ReviewRecord, StudyProfile
from .schemas import (
    DailyProgressPayload,
    MigrationPayload,
    ReviewPayload,
    SettingsPayload,
    StudyStateResponse,
)

app = FastAPI(title="Tango Anki API", version="1.0.0")
origins = [item.strip() for item in os.getenv("CORS_ORIGINS", "http://localhost:7777").split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def create_tables() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def validate_device_id(device_id: str) -> str:
    if not 8 <= len(device_id) <= 64 or not all(char.isalnum() or char in "-_" for char in device_id):
        raise HTTPException(status_code=400, detail="Invalid device id")
    return device_id


def get_or_create_profile(db: Session, device_id: str) -> StudyProfile:
    validate_device_id(device_id)
    profile = db.get(StudyProfile, device_id)
    if profile is None:
        profile = StudyProfile(device_id=device_id, settings={"newPerDay": 20}, daily_progress={})
        db.add(profile)
        db.flush()
    return profile


def to_review_payload(record: ReviewRecord) -> ReviewPayload:
    return ReviewPayload(
        state=record.state,
        due=record.due,
        interval=record.interval,
        ease=record.ease,
        reps=record.reps,
        lapses=record.lapses,
        step=record.step,
        lastReviewed=record.last_reviewed,
    )


def upsert_review(db: Session, device_id: str, card_id: str, payload: ReviewPayload) -> None:
    values = payload.model_dump()
    values["last_reviewed"] = values.pop("lastReviewed")
    insert_values = {"device_id": device_id, "card_id": card_id, **values}
    statement = insert(ReviewRecord).values(**insert_values)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[ReviewRecord.device_id, ReviewRecord.card_id],
            set_=values,
        )
    )


def state_response(db: Session, profile: StudyProfile) -> StudyStateResponse:
    records = db.scalars(select(ReviewRecord).where(ReviewRecord.device_id == profile.device_id)).all()
    daily = DailyProgressPayload.model_validate(profile.daily_progress) if profile.daily_progress else None
    return StudyStateResponse(
        reviews={record.card_id: to_review_payload(record) for record in records},
        daily=daily,
        settings=SettingsPayload.model_validate(profile.settings or {"newPerDay": 20}),
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/study-state/{device_id}", response_model=StudyStateResponse)
def get_state(device_id: str, db: Session = Depends(get_db)) -> StudyStateResponse:
    profile = get_or_create_profile(db, device_id)
    db.commit()
    return state_response(db, profile)


@app.post("/api/v1/study-state/{device_id}/migrate", response_model=StudyStateResponse)
def migrate(device_id: str, payload: MigrationPayload, db: Session = Depends(get_db)) -> StudyStateResponse:
    profile = get_or_create_profile(db, device_id)
    for card_id, review in payload.reviews.items():
        existing = db.get(ReviewRecord, (device_id, card_id))
        if existing is None or (review.lastReviewed or 0) >= (existing.last_reviewed or 0):
            upsert_review(db, device_id, card_id, review)
    if payload.daily and (
        not profile.daily_progress or payload.daily.date >= profile.daily_progress.get("date", "")
    ):
        profile.daily_progress = payload.daily.model_dump()
    if payload.settings:
        profile.settings = payload.settings.model_dump()
    db.commit()
    return state_response(db, profile)


@app.put("/api/v1/study-state/{device_id}/reviews/{card_id}", status_code=204)
def save_review(device_id: str, card_id: str, payload: ReviewPayload, db: Session = Depends(get_db)) -> None:
    get_or_create_profile(db, device_id)
    upsert_review(db, device_id, card_id, payload)
    db.commit()


@app.put("/api/v1/study-state/{device_id}/daily", status_code=204)
def save_daily(device_id: str, payload: DailyProgressPayload, db: Session = Depends(get_db)) -> None:
    profile = get_or_create_profile(db, device_id)
    profile.daily_progress = payload.model_dump()
    db.commit()


@app.put("/api/v1/study-state/{device_id}/settings", status_code=204)
def save_settings(device_id: str, payload: SettingsPayload, db: Session = Depends(get_db)) -> None:
    profile = get_or_create_profile(db, device_id)
    profile.settings = payload.model_dump()
    db.commit()
