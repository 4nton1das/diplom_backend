import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, DateTime, ForeignKey, Text, Float, BigInteger
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from modules.shared.database import Base


class Media(Base):
    __tablename__ = "media"
    __table_args__ = {"schema": "media"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auth.users.id", ondelete="CASCADE")
    )

    # Метаданные файла
    title: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(1024))
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)

    # Статусы
    status: Mapped[str] = mapped_column(
        String(50),
        default="uploaded"  # uploaded -> processing -> transcribed -> summarized -> failed
    )
    processing_stage: Mapped[Optional[str]] = mapped_column(
        String(50), default="media"  # media, asr, llm
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )

    # Связи
    processing_jobs: Mapped[list["ProcessingJob"]] = relationship(
        "ProcessingJob", back_populates="media", cascade="all, delete-orphan"
    )


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = {"schema": "media"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media.media.id", ondelete="CASCADE")
    )

    # Информация о задаче
    stage: Mapped[str] = mapped_column(String(50))  # asr, llm
    status: Mapped[str] = mapped_column(String(50))  # pending, processing, completed, failed
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(255))

    # Метрики
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    media: Mapped["Media"] = relationship("Media", back_populates="processing_jobs")
