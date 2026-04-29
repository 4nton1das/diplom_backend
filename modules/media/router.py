from fastapi import APIRouter, Depends, UploadFile, File, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid

import httpx
from pydantic import BaseModel
from modules.auth.dependencies import CurrentUser
from modules.shared.database import get_db_session
from modules.media.service import MediaService
from modules.media.models import Media, UserMedia, MediaSegment, ProcessingJob, ProcessingStage

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


@router.get("/{media_id}/status")
async def get_media_processing_status(
    media_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session)
):
    link_result = await db.execute(
        select(UserMedia).where(
            UserMedia.media_id == media_id,
            UserMedia.user_id == current_user.user_id,
        )
    )

    if not link_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Media not found or access denied")

    media_result = await db.execute(
        select(Media).where(Media.id == media_id)
    )
    media = media_result.scalar_one_or_none()

    if not media:
        raise HTTPException(status_code=404, detail="Media not found")

    job_result = await db.execute(
        select(ProcessingJob)
        .where(
            ProcessingJob.media_id == media_id,
            ProcessingJob.job_type == "asr",
        )
        .order_by(ProcessingJob.created_at.desc())
    )
    job = job_result.scalars().first()

    if not job:
        return {
            "media_id": media_id,
            "media_status": media.status.value,
            "job": None,
            "stages": [],
        }

    stages_result = await db.execute(
        select(ProcessingStage)
        .where(ProcessingStage.job_id == job.id)
        .order_by(ProcessingStage.created_at)
    )
    stages = stages_result.scalars().all()

    return {
        "media_id": media_id,
        "media_status": media.status.value,
        "job": {
            "id": job.id,
            "type": job.job_type,
            "status": job.status,
            "current_stage": job.current_stage,
            "progress": job.progress,
            "error_message": job.error_message,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "completed_at": job.completed_at,
        },
        "stages": [
            {
                "name": stage.stage_name,
                "status": stage.status,
                "progress": stage.progress,
                "meta": stage.meta,
                "error_message": stage.error_message,
                "started_at": stage.started_at,
                "completed_at": stage.completed_at,
            }
            for stage in stages
        ],
    }
