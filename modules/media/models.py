import enum
import uuid
from sqlalchemy import Column, String, ForeignKey, DateTime, Enum, Text, Integer, Float, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from modules.shared.database import Base


class MediaStatus(enum.Enum):
    PENDING = "pending"  # Добавлен в базу
    DOWNLOADING = "downloading"  # Скачивается (для Cobalt)
    PREPARING = "preparing"  # Нарезка и конвертация
    TRANSCRIBING = "transcribing"  # ASR в работе
    SUMMARIZING = "summarizing"  # LLM в работе (на будущее)
    COMPLETED = "completed"  # Полностью готов
    FAILED = "failed"  # Ошибка на любом этапе


class Media(Base):
    """Физический медиа-файл и его глобальные метаданные"""
    __tablename__ = "media"
    __table_args__ = {"schema": "media"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Хэш файла (SHA256) или ID из Rutube. Ключ для дедупликации!
    source_id = Column(String(255), unique=True, index=True)
    s3_key = Column(String(512))  # Путь в MinIO (например, hash.mp3)
    status = Column(Enum(MediaStatus), default=MediaStatus.PENDING)

    # Общий транскрипт (собирается воркером в конце)
    full_text = Column(Text, nullable=True)

    segments = relationship("MediaSegment", back_populates="media", cascade="all, delete-orphan")
    user_media = relationship("UserMedia", back_populates="media", cascade="all, delete-orphan")

    processing_jobs = relationship(
        "ProcessingJob",
        back_populates="media",
        cascade="all, delete-orphan"
    )


class UserMedia(Base):
    """Связь пользователя и медиа (у одного файла может быть много владельцев)"""
    __tablename__ = "user_media"
    __table_args__ = (
        UniqueConstraint("user_id", "media_id", name="uq_user_media_user_id_media_id"),
        {"schema": "media"},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("auth.users.id", ondelete="CASCADE"))
    media_id = Column(UUID(as_uuid=True), ForeignKey("media.media.id", ondelete="CASCADE"))

    # Индивидуальный конспект пользователя
    summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())

    media = relationship("Media", back_populates="user_media")


class MediaSegment(Base):
    """Нарезанные чанки для ASR"""
    __tablename__ = "media_segments"
    __table_args__ = {"schema": "media"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    media_id = Column(UUID(as_uuid=True), ForeignKey("media.media.id", ondelete="CASCADE"))
    position = Column(Integer)

    # Временные метки (в секундах от начала файла)
    start_time = Column(Float, nullable=True)
    end_time = Column(Float, nullable=True)

    text = Column(Text, nullable=True)

    # Детальные таймкоды слов: [{"word": "Привет", "start": 0.5, "end": 0.8}, ...]
    words = Column(JSONB, nullable=True)

    media = relationship("Media", back_populates="segments")


class ProcessingJob(Base):
    """
    Одна задача обработки.

    ASR job:
        общий для Media, запускается один раз

    SUMMARY job:
        будет пользовательским, добавим позже для LLM
    """
    __tablename__ = "processing_jobs"
    __table_args__ = {"schema": "media"}

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    media_id = Column(
        UUID(as_uuid=True),
        ForeignKey("media.media.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    job_type = Column(String(50), nullable=False, index=True)  # asr / summary
    status = Column(String(50), default="pending", nullable=False)  # pending / processing / completed / failed

    current_stage = Column(String(50), nullable=True)
    progress = Column(Integer, default=0, nullable=False)

    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    media = relationship("Media", back_populates="processing_jobs")
    stages = relationship(
        "ProcessingStage",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="ProcessingStage.created_at",
    )


class ProcessingStage(Base):
    """
    Конкретный этап внутри job.

    Для ASR:
        preparing
        transcribing
        finalizing

    Для LLM потом:
        llm_map
        llm_reduce
    """
    __tablename__ = "processing_stages"
    __table_args__ = (
        UniqueConstraint("job_id", "stage_name", name="uq_processing_stage_job_stage"),
        {"schema": "media"},
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    job_id = Column(
        UUID(as_uuid=True),
        ForeignKey("media.processing_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    stage_name = Column(String(50), nullable=False)
    status = Column(String(50), default="pending", nullable=False)  # pending / processing / completed / failed

    progress = Column(Integer, default=0, nullable=False)
    meta = Column(JSONB, nullable=True)

    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    job = relationship("ProcessingJob", back_populates="stages")
