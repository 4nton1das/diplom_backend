# modules/media/models.py
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, Float, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from modules.shared.database import Base


class Media(Base):
    __tablename__ = "media"
    __table_args__ = {"schema": "media"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("auth.users.id", ondelete="CASCADE"), index=True)

    # Файловые метаданные
    original_filename: Mapped[str] = mapped_column(String(512))
    file_path: Mapped[str] = mapped_column(String(1024))          # относительный путь: uploads/{user_id}/{file_id}.ext
    file_size: Mapped[int] = mapped_column(Integer)               # в байтах
    mime_type: Mapped[str] = mapped_column(String(100))
    checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)  # SHA256

    # Медиа-метаданные (извлекаются позже)
    duration: Mapped[Optional[int]] = mapped_column(Integer)     # в секундах
    format: Mapped[Optional[str]] = mapped_column(String(50))    # например, 'mp4', 'mp3'

    # Аудио метаданные
    sample_rate: Mapped[Optional[int]] = mapped_column(Integer)
    channels: Mapped[Optional[int]] = mapped_column(Integer)
    audio_codec: Mapped[Optional[str]] = mapped_column(String(50))

    # Видео метаданные
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    fps: Mapped[Optional[float]] = mapped_column(Float)
    video_codec: Mapped[Optional[str]] = mapped_column(String(50))

    # Статус обработки
    status: Mapped[str] = mapped_column(String(50), default="uploaded")  # uploaded → processing → transcribed → summarized → failed
    processing_stage: Mapped[Optional[str]] = mapped_column(String(50))  # 'media', 'asr', 'llm'
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Видимость
    visibility: Mapped[str] = mapped_column(String(20), default="private")  # private, public

    # Временные метки
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), onupdate=func.now())
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Связи
    processing_jobs: Mapped[list["ProcessingJob"]] = relationship("ProcessingJob", back_populates="media", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Media {self.id} - {self.original_filename}>"


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = {"schema": "media"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    media_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("media.media.id", ondelete="CASCADE"), index=True)

    stage: Mapped[str] = mapped_column(String(50))           # 'asr', 'llm'
    status: Mapped[str] = mapped_column(String(50))          # 'pending', 'processing', 'completed', 'failed'
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(255))

    # Метрики
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    media: Mapped["Media"] = relationship("Media", back_populates="processing_jobs")


class Transcription(Base):
    __tablename__ = "transcriptions"
    __table_args__ = {"schema": "media"}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    media_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("media.media.id", ondelete="CASCADE"), index=True)
    segments: Mapped[dict] = mapped_column(JSONB)  # список объектов {start, end, text}
    full_text: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    media: Mapped["Media"] = relationship("Media", backref="transcriptions")
