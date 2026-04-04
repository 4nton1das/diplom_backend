# modules/__init__.py
# Импортируем все модели для регистрации в SQLAlchemy Base

# Auth models
from modules.auth.models import User, RefreshToken

# Media models
from modules.media.models import Media, ProcessingJob, Transcription

# LLM models
from modules.llm.models import Summary

__all__ = [
    "User", "RefreshToken",
    "Media", "ProcessingJob", "Transcription",
    "Summary"
]
