from fastapi import APIRouter, Depends, UploadFile, File, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from modules.auth.dependencies import CurrentUser
from modules.shared.database import get_db_session
from modules.media.service import MediaService
from modules.media.schemas import MediaCreateResponse, MediaRead
from modules.media.models import Media

router = APIRouter(prefix="/media", tags=["media"])


@router.post("/upload", response_model=MediaCreateResponse, status_code=status.HTTP_201_CREATED)
async def upload_media(
    current_user: CurrentUser,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session)
):
    """Загрузка медиафайла"""
    service = MediaService(db)
    return await service.upload_media(current_user.user_id, file)


@router.get("/list", response_model=list[MediaRead])
async def list_media(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session),
    skip: int = 0,
    limit: int = 20
):
    """Получить список файлов текущего пользователя"""
    from sqlalchemy import select
    result = await db.execute(
        select(Media)
        .where(Media.user_id == current_user.user_id)
        .order_by(Media.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    media_list = result.scalars().all()
    return media_list


@router.get("/{media_id}/status", response_model=MediaRead)
async def get_media_status(
    media_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db_session)
):
    from sqlalchemy import select
    result = await db.execute(
        select(Media).where(Media.id == media_id, Media.user_id == current_user.user_id)
    )
    media = result.scalar_one_or_none()
    if not media:
        raise HTTPException(status_code=404, detail="Media not found")
    return media
