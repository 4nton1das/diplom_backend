import hashlib
import os
import uuid
from pathlib import Path

import aiofiles
from fastapi import UploadFile, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from modules.media.config import media_config
from modules.media.models import Media, ProcessingJob
from modules.media.schemas import MediaRead


class MediaService:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def upload_media(self, user_id: uuid.UUID, file: UploadFile) -> Media:
        # 1. Валидация расширения
        ext = file.filename.split(".")[-1].lower() if "." in file.filename else ""
        if ext not in media_config.allowed_extensions:
            raise HTTPException(status_code=400, detail="Unsupported file format")

        # 2. Генерируем ID и путь с подпапкой пользователя
        media_id = uuid.uuid4()
        user_upload_dir = Path(media_config.upload_dir) / str(user_id)
        user_upload_dir.mkdir(parents=True, exist_ok=True)

        safe_filename = f"{media_id}.{ext}"
        full_path = user_upload_dir / safe_filename

        # 3. Сохраняем файл и одновременно считаем хэш
        sha256 = hashlib.sha256()
        try:
            async with aiofiles.open(full_path, 'wb') as out_file:
                while chunk := await file.read(1024 * 1024):  # 1MB chunks
                    await out_file.write(chunk)
                    sha256.update(chunk)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"File save error: {str(e)}")

        # 4. Получаем размер и хэш
        file_size = full_path.stat().st_size
        file_hash = sha256.hexdigest()

        # 5. Запись в БД (новая модель)
        db_media = Media(
            id=media_id,
            user_id=user_id,
            original_filename=file.filename,
            file_path=str(full_path),  # относительный путь
            file_size=file_size,
            mime_type=file.content_type or "application/octet-stream",
            checksum=file_hash,
            status="uploaded",
            processing_stage=None,  # пока не начали обработку
            visibility="private"
        )

        self.db.add(db_media)

        # 6. Создаём задачу для ASR
        job = ProcessingJob(
            id=uuid.uuid4(),
            media_id=media_id,
            stage="asr",
            status="pending"
        )
        self.db.add(job)

        await self.db.commit()
        await self.db.refresh(db_media)

        # 7. TODO: запустить Celery задачу для ASR, передав media_id
        from modules.media.tasks import process_asr
        process_asr.delay(str(media_id))

        return db_media
