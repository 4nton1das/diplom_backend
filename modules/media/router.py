from fastapi import APIRouter, Depends, UploadFile, File, status, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid

from fastapi.encoders import jsonable_encoder
import httpx
import asyncio
from pydantic import BaseModel
from modules.auth.dependencies import CurrentUser
from modules.shared.database import get_db_session, AsyncSessionLocal
from modules.media.service import MediaService
from modules.media.models import Media, UserMedia, MediaSegment, ProcessingJob, ProcessingStage
from modules.auth.service import UserService

router = APIRouter(prefix="/media", tags=["media"])


async def serialize_job_with_stages(db: AsyncSession, job: ProcessingJob) -> dict:
    stages_result = await db.execute(
        select(ProcessingStage)
        .where(ProcessingStage.job_id == job.id)
        .order_by(ProcessingStage.created_at)
    )
    stages = stages_result.scalars().all()

    return {
        "id": job.id,
        "media_id": job.media_id,
        "user_id": job.user_id,
        "type": job.job_type,
        "status": job.status,
        "current_stage": job.current_stage,
        "progress": job.progress,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
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


async def check_user_media_access(
    db: AsyncSession,
    user_id: uuid.UUID,
    media_id: uuid.UUID,
) -> bool:
    result = await db.execute(
        select(UserMedia).where(
            UserMedia.user_id == user_id,
            UserMedia.media_id == media_id,
        )
    )
    return result.scalar_one_or_none() is not None


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_media(
    current_user: CurrentUser,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session)
):
    service = MediaService(db)
    media = await service.upload_media(current_user.user_id, file)
    return {"id": media.id, "status": media.status.value}


@router.get("/my")
async def get_my_media(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Список медиа текущего пользователя для страницы /summaries.
    """
    result = await db.execute(
        select(UserMedia, Media)
        .join(Media, UserMedia.media_id == Media.id)
        .where(UserMedia.user_id == current_user.user_id)
        .order_by(UserMedia.created_at.desc())
    )

    rows = result.all()

    return [
        {
            "id": media.id,
            "source_id": media.source_id,
            "status": media.status.value,
            "has_transcript": bool(media.full_text),
            "created_at": user_media.created_at,
        }
        for user_media, media in rows
    ]


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


@router.get("/{media_id}/jobs")
async def get_media_jobs(
    media_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Универсальный endpoint для frontend.

    Возвращает все задачи обработки по media:
    - ASR job
    - Summary jobs текущего пользователя
    """
    has_access = await check_user_media_access(
        db=db,
        user_id=current_user.user_id,
        media_id=media_id,
    )

    if not has_access:
        raise HTTPException(
            status_code=404,
            detail="Media not found or access denied",
        )

    media_result = await db.execute(
        select(Media).where(Media.id == media_id)
    )
    media = media_result.scalar_one_or_none()

    if not media:
        raise HTTPException(status_code=404, detail="Media not found")

    jobs_result = await db.execute(
        select(ProcessingJob)
        .where(
            ProcessingJob.media_id == media_id,
            (
                (ProcessingJob.job_type == "asr")
                | (
                    (ProcessingJob.job_type == "summary")
                    & (ProcessingJob.user_id == current_user.user_id)
                )
            ),
        )
        .order_by(ProcessingJob.created_at.desc())
    )

    jobs = jobs_result.scalars().all()

    return {
        "media": {
            "id": media.id,
            "status": media.status.value,
            "has_transcript": bool(media.full_text),
        },
        "jobs": [
            await serialize_job_with_stages(db, job)
            for job in jobs
        ],
    }


@router.websocket("/ws/jobs/{job_id}")
async def websocket_job_status(
    websocket: WebSocket,
    job_id: uuid.UUID,
    token: str,
):
    """
    WebSocket для real-time статуса job.

    Подключение:
    ws://localhost:8000/media/ws/jobs/{job_id}?token=<access_token>
    """
    await websocket.accept()

    db = AsyncSessionLocal()

    try:
        try:
            token_data = await UserService.verify_token(token)
        except Exception:
            await websocket.send_json({
                "type": "error",
                "message": "Invalid token",
            })
            await websocket.close(code=1008)
            return

        while True:
            # Важно: сбрасываем identity map, чтобы видеть свежие изменения,
            # которые Celery пишет в другой сессии.
            db.expire_all()

            job_result = await db.execute(
                select(ProcessingJob).where(ProcessingJob.id == job_id)
            )
            job = job_result.scalar_one_or_none()

            if not job:
                await websocket.send_json({
                    "type": "error",
                    "message": "Job not found",
                })
                await websocket.close(code=1008)
                return

            has_access = await check_user_media_access(
                db=db,
                user_id=token_data.user_id,
                media_id=job.media_id,
            )

            if not has_access:
                await websocket.send_json({
                    "type": "error",
                    "message": "Access denied",
                })
                await websocket.close(code=1008)
                return

            if job.job_type == "summary" and job.user_id != token_data.user_id:
                await websocket.send_json({
                    "type": "error",
                    "message": "Access denied",
                })
                await websocket.close(code=1008)
                return

            payload = await serialize_job_with_stages(db, job)

            await websocket.send_json(
                jsonable_encoder({
                    "type": "job_status",
                    "job": payload,
                })
            )

            if job.status in ("completed", "failed"):
                await websocket.send_json(
                    jsonable_encoder({
                        "type": "job_finished",
                        "status": job.status,
                    })
                )
                await websocket.close(code=1000)
                return

            await asyncio.sleep(1)

    except WebSocketDisconnect:
        return

    finally:
        await db.close()
