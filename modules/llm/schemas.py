# modules/llm/schemas.py

import uuid
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class SummaryCreateResponse(BaseModel):
    summary_id: uuid.UUID
    job_id: uuid.UUID
    media_id: uuid.UUID
    user_id: uuid.UUID
    status: str


class SummaryRead(BaseModel):
    id: uuid.UUID
    media_id: uuid.UUID
    user_id: uuid.UUID
    job_id: Optional[uuid.UUID] = None

    title: Optional[str] = None
    content: Optional[str] = None
    content_json: Optional[dict] = None

    status: str
    model_name: Optional[str] = None
    provider: Optional[str] = None
    prompt_version: Optional[str] = None

    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    tokens_total: Optional[int] = None

    error_message: Optional[str] = None

    created_at: datetime
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SummaryChunkRead(BaseModel):
    id: uuid.UUID
    summary_id: uuid.UUID
    job_id: Optional[uuid.UUID] = None

    position: int
    start_time: Optional[float] = None
    end_time: Optional[float] = None

    source_text: str
    summary_text: Optional[str] = None

    status: str
    error_message: Optional[str] = None

    created_at: datetime
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ProcessingStageRead(BaseModel):
    name: str
    status: str
    progress: int
    meta: Optional[dict] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ProcessingJobRead(BaseModel):
    id: uuid.UUID
    media_id: uuid.UUID
    user_id: Optional[uuid.UUID] = None

    type: str
    status: str
    current_stage: Optional[str] = None
    progress: int
    error_message: Optional[str] = None

    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    stages: List[ProcessingStageRead] = []


class SummaryWithJobRead(BaseModel):
    summary: SummaryRead
    job: Optional[ProcessingJobRead] = None
    chunks: List[SummaryChunkRead] = []
