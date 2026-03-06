import os
import aiofiles
from fastapi import UploadFile, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from modules.media.models import Media
from modules.media.config import media_config
from modules.media.schemas import MediaRead


# Импортируем шину событий (пока in-memory)
# from modules.shared.event_bus import event_bus

class MediaService:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def upload_media(self, user_id: uuid.UUID, file: UploadFile) -> Media:
        # 1. Валидация расширения
        ext = file.filename.split(".")[-1].lower() if "." in file.filename else ""
        if ext not in media_config.allowed_extensions:
            raise HTTPException(status_code=400, detail="Unsupported file format")

        # 2. Генерируем путь
        media_id = uuid.uuid4()
        safe_filename = f"{media_id}.{ext}"
        upload_path = media_config.get_upload_path()
        full_path = os.path.join(upload_path, safe_filename)

        # 3. Сохраняем файл на диск (асинхронно, чанками)
        try:
            async with aiofiles.open(full_path, 'wb') as out_file:
                while content := await file.read(1024 * 1024):  # Читаем по 1МБ
                    await out_file.write(content)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"File save error: {str(e)}")

        # 4. Получаем размер
        file_size = os.path.getsize(full_path)

        # 5. Запись в БД
        db_media = Media(
            id=media_id,
            user_id=user_id,
            title=file.filename,
            original_filename=file.filename,
            file_path=full_path,
            content_type=file.content_type,
            size_bytes=file_size,
            status="uploaded",
            processing_stage="media"
        )

        self.db.add(db_media)
        await self.db.commit()
        await self.db.refresh(db_media)

        # 6. TODO: Отправить событие MediaUploaded в EventBus
        # await event_bus.publish(MediaUploaded(...))

        return db_media
