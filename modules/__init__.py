# modules/__init__.py
# Импортируем все модели для регистрации в SQLAlchemy Base

# Auth models
from modules.auth.models import User, RefreshToken

# Media models
from modules.media.models import Media, UserMedia, MediaSegment, ProcessingJob, ProcessingStage

# LLM models
from modules.llm.models import Summary, SummaryChunk

__all__ = [
    "User", "RefreshToken",
    "Media", "UserMedia", "MediaSegment",
    "ProcessingJob", "ProcessingStage",
    "Summary", "SummaryChunk"
]
