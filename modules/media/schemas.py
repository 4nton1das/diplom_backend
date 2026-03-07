import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class MediaRead(BaseModel):
    id: uuid.UUID
    original_filename: str
    file_size: int
    mime_type: str
    duration: Optional[int] = None
    status: str
    processing_stage: Optional[str] = None
    visibility: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class MediaCreateResponse(MediaRead):
    pass
