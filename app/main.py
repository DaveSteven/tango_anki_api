import os
import hashlib
import hmac
import secrets
from collections.abc import Generator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import text, select
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine
from .models import ReviewRecord, StudyProfile, User
from .schemas import (
    DailyProgressPayload,
    LoginPayload,
    LoginResponse,
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
bearer_scheme = HTTPBearer(auto_error=False)

DEFAULT_USERNAME = os.getenv("DEFAULT_USERNAME", "david")
DEFAULT_PASSWORD = os.getenv("DEFAULT_PASSWORD", "214423")
LEGACY_DEVICE_ID = os.getenv("LEGACY_DEVICE_ID", "david-local")
PASSWORD_SALT = os.getenv("PASSWORD_SALT", "tango-anki-local-salt")


@app.on_event("startup")
def create_tables() -> None:
    Base.metadata.create_all(bind=engine)
    seed_default_user_and_migrate_legacy_data()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def password_hash(password: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), PASSWORD_SALT.encode(), 120_000)
    return digest.hex()


def verify_password(password: str, stored_hash: str) -> bool:
    return hmac.compare_digest(password_hash(password), stored_hash)


def validate_username(username: str) -> str:
    if not 1 <= len(username) <= 64 or not all(char.isalnum() or char in "-_" for char in username):
        raise HTTPException(status_code=400, detail="Invalid username")
    return username


def seed_default_user_and_migrate_legacy_data() -> None:
    db = SessionLocal()
    try:
        username = validate_username(DEFAULT_USERNAME)
        user = db.get(User, username)
        if user is None:
            db.add(User(username=username, password_hash=password_hash(DEFAULT_PASSWORD)))
            db.flush()
        else:
            user.password_hash = password_hash(DEFAULT_PASSWORD)

        legacy = db.get(StudyProfile, LEGACY_DEVICE_ID)
        current = db.get(StudyProfile, username)
        if legacy is not None:
            if current is None:
                current = StudyProfile(
                    device_id=username,
                    settings=legacy.settings or {"newPerDay": 20},
                    daily_progress=legacy.daily_progress or {},
                )
                db.add(current)
                db.flush()
            elif legacy.daily_progress and not current.daily_progress:
                current.daily_progress = legacy.daily_progress
            if legacy.settings and not current.settings:
                current.settings = legacy.settings

            db.execute(
                text(
                    """
                    INSERT INTO review_records (
                        device_id, card_id, state, due, interval, ease, reps, lapses, step, last_reviewed
                    )
                    SELECT
                        :username, card_id, state, due, interval, ease, reps, lapses, step, last_reviewed
                    FROM review_records
                    WHERE device_id = :legacy_device_id
                    ON CONFLICT (device_id, card_id) DO UPDATE SET
                        state = EXCLUDED.state,
                        due = EXCLUDED.due,
                        interval = EXCLUDED.interval,
                        ease = EXCLUDED.ease,
                        reps = EXCLUDED.reps,
                        lapses = EXCLUDED.lapses,
                        step = EXCLUDED.step,
                        last_reviewed = EXCLUDED.last_reviewed
                    WHERE COALESCE(EXCLUDED.last_reviewed, 0) >= COALESCE(review_records.last_reviewed, 0)
                    """
                ),
                {"username": username, "legacy_device_id": LEGACY_DEVICE_ID},
            )
            db.execute(text("DELETE FROM review_records WHERE device_id = :legacy_device_id"), {"legacy_device_id": LEGACY_DEVICE_ID})
            db.delete(legacy)
        elif current is None:
            db.add(StudyProfile(device_id=username, settings={"newPerDay": 20}, daily_progress={}))
        db.commit()
    finally:
        db.close()


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Missing token")
    user = db.scalar(select(User).where(User.token == credentials.credentials))
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


def get_or_create_profile(db: Session, username: str) -> StudyProfile:
    validate_username(username)
    profile = db.get(StudyProfile, username)
    if profile is None:
        profile = StudyProfile(device_id=username, settings={"newPerDay": 20}, daily_progress={})
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


@app.post("/api/v1/auth/login", response_model=LoginResponse)
def login(payload: LoginPayload, db: Session = Depends(get_db)) -> LoginResponse:
    username = validate_username(payload.username)
    user = db.get(User, username)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    user.token = secrets.token_urlsafe(48)
    db.commit()
    return LoginResponse(token=user.token, username=user.username)


@app.post("/api/v1/auth/logout", status_code=204)
def logout(user: User = Depends(current_user), db: Session = Depends(get_db)) -> None:
    user.token = None
    db.commit()


@app.get("/api/v1/study-state/me", response_model=StudyStateResponse)
def get_state(user: User = Depends(current_user), db: Session = Depends(get_db)) -> StudyStateResponse:
    profile = get_or_create_profile(db, user.username)
    db.commit()
    return state_response(db, profile)


@app.put("/api/v1/study-state/me/reviews/{card_id}", status_code=204)
def save_review(
    card_id: str,
    payload: ReviewPayload,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    get_or_create_profile(db, user.username)
    upsert_review(db, user.username, card_id, payload)
    db.commit()


@app.put("/api/v1/study-state/me/daily", status_code=204)
def save_daily(
    payload: DailyProgressPayload,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    profile = get_or_create_profile(db, user.username)
    profile.daily_progress = payload.model_dump()
    db.commit()


@app.put("/api/v1/study-state/me/settings", status_code=204)
def save_settings(
    payload: SettingsPayload,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    profile = get_or_create_profile(db, user.username)
    profile.settings = payload.model_dump()
    db.commit()
