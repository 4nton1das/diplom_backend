import hashlib
import uuid
import os
import tempfile
from fastapi import UploadFile, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from modules.media.models import Media, UserMedia, MediaStatus
from modules.media.storage import s3_storage


class MediaService:
    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def upload_media(self, user_id: uuid.UUID, file: UploadFile) -> Media:
        # 1. Считаем хэш файла прямо в памяти/во временный файл
        sha256 = hashlib.sha256()

        fd, temp_path = tempfile.mkstemp(suffix=".tmp")
        try:
            with os.fdopen(fd, 'wb') as out_file:
                while chunk := await file.read(1024 * 1024):
                    out_file.write(chunk)
                    sha256.update(chunk)

            file_hash = sha256.hexdigest()

            # 2. ДЕДУПЛИКАЦИЯ: Ищем файл по хэшу в БД
            result = await self.db.execute(select(Media).where(Media.source_id == file_hash))
            existing_media = result.scalar_one_or_none()

            if existing_media:
                # Файл уже есть! Просто создаем связь для этого юзера
                print(f"File {file_hash} already exists. Linking to user.")
                user_link = UserMedia(user_id=user_id, media_id=existing_media.id)
                self.db.add(user_link)
                await self.db.commit()
                return existing_media

            # 3. Если файла нет - загружаем в MinIO
            media_id = uuid.uuid4()
            ext = file.filename.split(".")[-1].lower() if "." in file.filename else "bin"
            s3_key = f"{file_hash}.{ext}"  # Имя файла в бакете = его хэш

            # Загружаем из временного файла в S3
            s3_storage.upload_file(temp_path, s3_key)

            # 4. Запись в БД
            new_media = Media(
                id=media_id,
                source_id=file_hash,
                s3_key=s3_key,
                status=MediaStatus.PENDING
            )
            self.db.add(new_media)

            # Привязываем к юзеру
            user_link = UserMedia(user_id=user_id, media_id=media_id)
            self.db.add(user_link)

            await self.db.commit()

            # 5. Запуск Celery
            from modules.asr.tasks import process_media_task
            process_media_task.delay(str(media_id))

            return new_media

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Upload error: {str(e)}")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
