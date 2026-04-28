import enum
import uuid
from sqlalchemy import Column, String, ForeignKey, DateTime, Enum, Text, Integer, Float
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


class UserMedia(Base):
    """Связь пользователя и медиа (у одного файла может быть много владельцев)"""
    __tablename__ = "user_media"
    __table_args__ = {"schema": "media"}

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
