from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class StudyProfile(Base):
    __tablename__ = "study_profiles"

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    daily_progress: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    reviews: Mapped[list["ReviewRecord"]] = relationship(cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    token: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ReviewRecord(Base):
    __tablename__ = "review_records"

    device_id: Mapped[str] = mapped_column(
        ForeignKey("study_profiles.device_id", ondelete="CASCADE"), primary_key=True
    )
    card_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(20))
    due: Mapped[int] = mapped_column(BigInteger)
    interval: Mapped[int] = mapped_column(Integer, default=0)
    ease: Mapped[float] = mapped_column(Float, default=2.5)
    reps: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    step: Mapped[int] = mapped_column(Integer, default=0)
    last_reviewed: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
