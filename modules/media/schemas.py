import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class MediaRead(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    processing_stage: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class MediaCreateResponse(MediaRead):
    pass
