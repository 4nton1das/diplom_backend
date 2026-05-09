# modules/llm/models.py

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    String,
    Integer,
    Text,
    DateTime,
    ForeignKey,
    Float,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from modules.shared.database import Base


class SummaryStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class SummaryChunkStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class Summary(Base):
    """
    Пользовательский конспект.

    Важно:
    - Media может иметь много Summary
    - User может иметь много Summary
    - Summary создается через ProcessingJob(job_type='summary')
    """
    __tablename__ = "summaries"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "media_id",
            name="uq_summaries_user_id_media_id",
        ),
        {"schema": "media"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media.media.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media.processing_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    content: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )  # Markdown

    content_json: Mapped[Optional[dict]] = mapped_column(
        JSONB,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(50),
        default=SummaryStatus.pending.value,
        nullable=False,
        index=True,
    )

    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    model_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    prompt_version: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    tokens_input: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tokens_output: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tokens_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        onupdate=func.now(),
        nullable=True,
    )

    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    media = relationship(
        "Media",
        lazy="selectin",
    )

    chunks: Mapped[list["SummaryChunk"]] = relationship(
        "SummaryChunk",
        back_populates="summary",
        cascade="all, delete-orphan",
        order_by="SummaryChunk.position",
    )

    def __repr__(self):
        return f"<Summary {self.id} user={self.user_id} media={self.media_id} status={self.status}>"


class SummaryChunk(Base):
    """
    Промежуточный результат LLM map-этапа.

    Один Summary состоит из нескольких SummaryChunk.
    Потом reduce собирает их в итоговый markdown.
    """
    __tablename__ = "summary_chunks"
    __table_args__ = (
        UniqueConstraint(
            "summary_id",
            "position",
            name="uq_summary_chunks_summary_id_position",
        ),
        {"schema": "media"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    summary_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media.summaries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media.processing_jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    position: Mapped[int] = mapped_column(Integer, nullable=False)

    start_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    end_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    summary_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True,)

    status: Mapped[str] = mapped_column(
        String(50),
        default=SummaryChunkStatus.pending.value,
        nullable=False,
        index=True,
    )

    tokens_input: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tokens_output: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    summary: Mapped["Summary"] = relationship(
        "Summary",
        back_populates="chunks",
    )

    def __repr__(self):
        return f"<SummaryChunk {self.id} summary={self.summary_id} pos={self.position} status={self.status}>"
