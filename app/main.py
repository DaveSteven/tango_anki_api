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
    migrate_user_id_schema()
    seed_default_user()


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


def migrate_user_id_schema() -> None:
    default_username = validate_username(DEFAULT_USERNAME).replace("'", "''")
    legacy_device_id = validate_username(LEGACY_DEVICE_ID).replace("'", "''")
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'public' AND table_name = 'users'
                    ) AND NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'id'
                    ) THEN
                        CREATE SEQUENCE IF NOT EXISTS users_id_seq;
                        ALTER TABLE users ADD COLUMN id integer;
                        ALTER TABLE users ALTER COLUMN id SET DEFAULT nextval('users_id_seq');
                        UPDATE users SET id = nextval('users_id_seq') WHERE id IS NULL;
                        PERFORM setval('users_id_seq', GREATEST((SELECT COALESCE(MAX(id), 1) FROM users), 1));
                        ALTER TABLE users ALTER COLUMN id SET NOT NULL;
                        ALTER SEQUENCE users_id_seq OWNED BY users.id;

                        IF EXISTS (
                            SELECT 1 FROM information_schema.table_constraints
                            WHERE table_schema = 'public'
                                AND table_name = 'users'
                                AND constraint_name = 'users_pkey'
                        ) THEN
                            ALTER TABLE users DROP CONSTRAINT users_pkey;
                        END IF;

                        ALTER TABLE users ADD CONSTRAINT users_pkey PRIMARY KEY (id);
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint WHERE conname = 'users_username_key'
                        ) THEN
                            ALTER TABLE users ADD CONSTRAINT users_username_key UNIQUE (username);
                        END IF;
                    END IF;

                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = 'study_profiles' AND column_name = 'device_id'
                    ) AND NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = 'study_profiles' AND column_name = 'user_id'
                    ) THEN
                        ALTER TABLE study_profiles ADD COLUMN user_id integer;

                        UPDATE study_profiles profile
                        SET user_id = users.id
                        FROM users
                        WHERE profile.device_id = users.username;

                        UPDATE study_profiles profile
                        SET user_id = users.id
                        FROM users
                        WHERE profile.device_id = '{legacy_device_id}'
                            AND users.username = '{default_username}'
                            AND profile.user_id IS NULL;

                        ALTER TABLE study_profiles ALTER COLUMN user_id SET NOT NULL;

                        IF EXISTS (
                            SELECT 1 FROM information_schema.table_constraints
                            WHERE table_schema = 'public'
                                AND table_name = 'review_records'
                                AND constraint_name = 'review_records_device_id_fkey'
                        ) THEN
                            ALTER TABLE review_records DROP CONSTRAINT review_records_device_id_fkey;
                        END IF;

                        IF EXISTS (
                            SELECT 1 FROM information_schema.table_constraints
                            WHERE table_schema = 'public'
                                AND table_name = 'study_profiles'
                                AND constraint_name = 'study_profiles_pkey'
                        ) THEN
                            ALTER TABLE study_profiles DROP CONSTRAINT study_profiles_pkey;
                        END IF;

                        ALTER TABLE study_profiles ADD CONSTRAINT study_profiles_pkey PRIMARY KEY (user_id);
                        ALTER TABLE study_profiles
                            ADD CONSTRAINT study_profiles_user_id_fkey
                            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;
                    END IF;

                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = 'review_records' AND column_name = 'device_id'
                    ) AND NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = 'review_records' AND column_name = 'user_id'
                    ) THEN
                        ALTER TABLE review_records ADD COLUMN user_id integer;

                        UPDATE review_records review
                        SET user_id = profile.user_id
                        FROM study_profiles profile
                        WHERE review.device_id = profile.device_id;

                        ALTER TABLE review_records ALTER COLUMN user_id SET NOT NULL;

                        IF EXISTS (
                            SELECT 1 FROM information_schema.table_constraints
                            WHERE table_schema = 'public'
                                AND table_name = 'review_records'
                                AND constraint_name = 'review_records_pkey'
                        ) THEN
                            ALTER TABLE review_records DROP CONSTRAINT review_records_pkey;
                        END IF;

                        ALTER TABLE review_records ADD CONSTRAINT review_records_pkey PRIMARY KEY (user_id, card_id);
                        ALTER TABLE review_records
                            ADD CONSTRAINT review_records_user_id_fkey
                            FOREIGN KEY (user_id) REFERENCES study_profiles(user_id) ON DELETE CASCADE;
                        ALTER TABLE review_records DROP COLUMN device_id;
                    END IF;

                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = 'study_profiles' AND column_name = 'device_id'
                    ) AND EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = 'study_profiles' AND column_name = 'user_id'
                    ) THEN
                        ALTER TABLE study_profiles DROP COLUMN device_id;
                    END IF;
                END $$;
                """
            )
        )


def seed_default_user() -> None:
    db = SessionLocal()
    try:
        username = validate_username(DEFAULT_USERNAME)
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(username=username, password_hash=password_hash(DEFAULT_PASSWORD))
            db.add(user)
            db.flush()
        else:
            user.password_hash = password_hash(DEFAULT_PASSWORD)
        if db.get(StudyProfile, user.id) is None:
            db.add(StudyProfile(user_id=user.id, settings={"newPerDay": 20}, daily_progress={}))
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


def get_or_create_profile(db: Session, user_id: int) -> StudyProfile:
    profile = db.get(StudyProfile, user_id)
    if profile is None:
        profile = StudyProfile(user_id=user_id, settings={"newPerDay": 20}, daily_progress={})
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


def upsert_review(db: Session, user_id: int, card_id: str, payload: ReviewPayload) -> None:
    values = payload.model_dump()
    values["last_reviewed"] = values.pop("lastReviewed")
    insert_values = {"user_id": user_id, "card_id": card_id, **values}
    statement = insert(ReviewRecord).values(**insert_values)
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[ReviewRecord.user_id, ReviewRecord.card_id],
            set_=values,
        )
    )


def state_response(db: Session, profile: StudyProfile) -> StudyStateResponse:
    records = db.scalars(select(ReviewRecord).where(ReviewRecord.user_id == profile.user_id)).all()
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
    user = db.scalar(select(User).where(User.username == username))
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
    profile = get_or_create_profile(db, user.id)
    db.commit()
    return state_response(db, profile)


@app.put("/api/v1/study-state/me/reviews/{card_id}", status_code=204)
def save_review(
    card_id: str,
    payload: ReviewPayload,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    get_or_create_profile(db, user.id)
    upsert_review(db, user.id, card_id, payload)
    db.commit()


@app.put("/api/v1/study-state/me/daily", status_code=204)
def save_daily(
    payload: DailyProgressPayload,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    profile = get_or_create_profile(db, user.id)
    profile.daily_progress = payload.model_dump()
    db.commit()


@app.put("/api/v1/study-state/me/settings", status_code=204)
def save_settings(
    payload: SettingsPayload,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> None:
    profile = get_or_create_profile(db, user.id)
    profile.settings = payload.model_dump()
    db.commit()
