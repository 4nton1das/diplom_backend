# modules/llm/models.py
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
import enum

from modules.media.models import Media
from modules.shared.database import Base


class SummaryStatus(str, enum.Enum):
    """Статусы генерации конспекта"""
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class Summary(Base):
    __tablename__ = "summaries"
    __table_args__ = {"schema": "media"}

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media.media.id", ondelete="CASCADE"),
        index=True,
        unique=True  # Один конспект на один медиафайл
    )

    # Контент конспекта
    content: Mapped[str] = mapped_column(Text)  # Markdown контент
    content_json: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True
    )  # Структурированные данные для фронтенда

    # Метаданные
    status: Mapped[str] = mapped_column(
        String(50), default=SummaryStatus.pending
    )
    model_name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(50))
    tokens_used: Mapped[Optional[int]] = mapped_column(Integer)

    # Ошибки
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Временные метки
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Связи
    media: Mapped["Media"] = relationship("Media", backref="summary")

    def __repr__(self):
        return f"<Summary {self.id} - {self.status}>"
