from fastapi import APIRouter, Depends, UploadFile, File, status
from sqlalchemy.ext.asyncio import AsyncSession

from modules.auth.dependencies import CurrentUser
from modules.shared.database import get_db_session
from modules.media.service import MediaService
from modules.media.schemas import MediaCreateResponse

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
