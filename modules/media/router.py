from fastapi import APIRouter, Depends, UploadFile, File, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid

import httpx
from pydantic import BaseModel
from modules.auth.dependencies import CurrentUser
from modules.shared.database import get_db_session
from modules.media.service import MediaService
from modules.media.models import Media, UserMedia, MediaSegment

router = APIRouter(prefix="/media", tags=["media"])


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_media(
    current_user: CurrentUser,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session)
):
    service = MediaService(db)
    media = await service.upload_media(current_user.user_id, file)
    return {"id": media.id, "status": media.status.value}


@router.get("/{media_id}/transcription")
async def get_transcription(
        media_id: uuid.UUID,
        current_user: CurrentUser,
        db: AsyncSession = Depends(get_db_session)
):
    """Получить транскрипцию (проверяем права через UserMedia)"""
    link_result = await db.execute(
        select(UserMedia).where(UserMedia.media_id == media_id, UserMedia.user_id == current_user.user_id)
    )
    if not link_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Media not found or access denied")

    media_result = await db.execute(select(Media).where(Media.id == media_id))
    media = media_result.scalar_one_or_none()

    if media.status.value not in ["completed", "summarizing"]:
        raise HTTPException(status_code=400, detail=f"Transcription not ready. Status: {media.status.value}")

    # Достаем сегменты
    seg_result = await db.execute(
        select(MediaSegment).where(MediaSegment.media_id == media_id).order_by(MediaSegment.position)
    )
    segments = [{"position": s.position, "text": s.text} for s in seg_result.scalars().all()]

    return {
        "full_text": media.full_text,
        "segments": segments
    }


class VideoUrlRequest(BaseModel):
    url: str


@router.post("/process-url")
async def process_video_url(
        request: VideoUrlRequest,
        _: CurrentUser  # Защищаем эндпоинт авторизацией
):
    """
    Запрашивает у Cobalt прямую ссылку на аудио.
    Браузер пользователя затем сам скачает этот файл.
    """
    COBALT_LOCAL = "http://localhost:9000/"

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                COBALT_LOCAL,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json={
                    "url": request.url,
                    "downloadMode": "audio",
                    "audioFormat": "mp3"
                }
            )
            data = response.json()

            # Проверяем, не вернул ли Cobalt ошибку в формате JSON
            if data.get("status") == "error":
                raise HTTPException(status_code=400, detail=data.get("text", "Cobalt error"))

            return data

        except httpx.RequestError as e:
            raise HTTPException(status_code=503, detail=f"Docker Cobalt недоступен: {str(e)}")
