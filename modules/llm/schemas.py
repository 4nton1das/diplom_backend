# modules/llm/schemas.py
import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class SummaryRead(BaseModel):
    """Схема для чтения конспекта"""
    id: uuid.UUID
    media_id: uuid.UUID
    content: str
    status: str
    model_name: str
    created_at: datetime
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SummaryChunk(BaseModel):
    """Чанк транскрипции для обработки"""
    chunk_id: int
    text: str
    start_time: float  # секунды
    end_time: float


class SummaryKeyPoint(BaseModel):
    """Ключевая точка в конспекте"""
    timestamp: str  # "00:05:23"
    text: str
    type: str = "concept"  # concept, definition, example, important
    section: Optional[str] = None


class SummaryStructured(BaseModel):
    """Структурированный конспект для фронтенда"""
    title: str
    duration: str
    topics: List[str]
    key_points: List[SummaryKeyPoint]
    sections: List[dict]
    summary: str


class LLMProcessRequest(BaseModel):
    """Запрос на обработку LLM (для тестового эндпоинта)"""
    media_id: uuid.UUID
