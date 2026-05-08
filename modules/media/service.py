import hashlib
import uuid
import os
import tempfile
import re
from urllib.parse import urlparse

from fastapi import UploadFile, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from modules.media.models import Media, UserMedia, MediaStatus, ProcessingJob
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
                await self._link_media_to_user(user_id, existing_media.id)
                return existing_media

            # 3. Если файла нет - загружаем в MinIO
            media_id = uuid.uuid4()
            ext = file.filename.split(".")[-1].lower() if file.filename and "." in file.filename else "bin"
            s3_key = f"{file_hash}.{ext}"
            file_size = os.path.getsize(temp_path)

            # Загружаем из временного файла в S3
            s3_storage.upload_file(temp_path, s3_key)

            # 4. Запись в БД
            new_media = Media(
                id=media_id,
                source_id=file_hash,
                source_type="file",
                s3_key=s3_key,
                title=file.filename,
                original_filename=file.filename,
                mime_type=file.content_type,
                file_size=file_size,
                status=MediaStatus.PENDING,
            )
            self.db.add(new_media)

            asr_job = ProcessingJob(
                media_id=media_id,
                user_id=None,
                job_type="asr",
                status="pending",
                progress=0,
            )
            self.db.add(asr_job)

            # Привязываем к юзеру
            user_link = UserMedia(user_id=user_id, media_id=media_id)
            self.db.add(user_link)

            await self.db.commit()
            await self.db.refresh(asr_job)

            # 5. Запуск Celery
            from modules.asr.tasks import process_media_task
            process_media_task.delay(str(media_id), str(asr_job.id))

            return new_media

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Upload error: {str(e)}")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    async def prepare_rutube_media(self, user_id: uuid.UUID, url: str) -> dict:
        """
        Проверка Rutube-ссылки перед скачиванием аудио на клиенте.

        Вариант B:
        - backend НЕ вызывает Cobalt;
        - backend НЕ скачивает аудио;
        - backend только проверяет дедупликацию по source_id;
        - если media уже есть, возвращает existing;
        - если media нет, frontend сам вызывает Cobalt и скачивает аудио.
        """
        video_id = self.extract_rutube_video_id(url)
        source_id = f"rutube:{video_id}"

        result = await self.db.execute(
            select(Media).where(Media.source_id == source_id)
        )
        existing_media = result.scalar_one_or_none()

        if existing_media:
            await self._link_media_to_user(user_id, existing_media.id)

            return {
                "status": "existing",
                "media": {
                    "id": existing_media.id,
                    "status": existing_media.status.value,
                    "source_type": existing_media.source_type,
                    "title": existing_media.title,
                    "embed_url": existing_media.embed_url,
                    "has_transcript": bool(existing_media.full_text),
                },
            }

        return {
            "status": "needs_upload",
            "video_id": video_id,
            "source_id": source_id,
            "original_url": url,
            "embed_url": self.build_rutube_embed_url(video_id),
        }

    async def complete_rutube_media(
            self,
            user_id: uuid.UUID,
            url: str,
            file: UploadFile,
    ) -> Media:
        video_id = self.extract_rutube_video_id(url)
        source_id = f"rutube:{video_id}"

        result = await self.db.execute(
            select(Media).where(Media.source_id == source_id)
        )
        existing_media = result.scalar_one_or_none()

        if existing_media:
            await self._link_media_to_user(user_id, existing_media.id)
            return existing_media

        fd, temp_path = tempfile.mkstemp(suffix=".mp3")

        try:
            file_size = 0

            with os.fdopen(fd, "wb") as out_file:
                while chunk := await file.read(1024 * 1024):
                    out_file.write(chunk)
                    file_size += len(chunk)

            if file_size == 0:
                raise HTTPException(status_code=400, detail="Uploaded audio is empty")

            s3_key = f"rutube/{video_id}.mp3"
            s3_storage.upload_file(temp_path, s3_key)

            media_id = uuid.uuid4()

            new_media = Media(
                id=media_id,
                source_id=source_id,
                source_type="rutube",
                original_url=url,
                embed_url=self.build_rutube_embed_url(video_id),
                title=file.filename or f"Rutube video {video_id}",
                original_filename=file.filename,
                s3_key=s3_key,
                mime_type=file.content_type or "audio/mpeg",
                file_size=file_size,
                status=MediaStatus.PENDING,
            )

            self.db.add(new_media)

            asr_job = ProcessingJob(
                media_id=media_id,
                user_id=None,
                job_type="asr",
                status="pending",
                progress=0,
            )

            self.db.add(asr_job)
            self.db.add(UserMedia(user_id=user_id, media_id=media_id))

            await self.db.commit()
            await self.db.refresh(asr_job)

            from modules.asr.tasks import process_media_task
            process_media_task.delay(str(media_id), str(asr_job.id))

            return new_media

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Rutube upload error: {str(e)}")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    async def _link_media_to_user(self, user_id: uuid.UUID, media_id: uuid.UUID) -> None:
        link_result = await self.db.execute(
            select(UserMedia).where(
                UserMedia.user_id == user_id,
                UserMedia.media_id == media_id,
            )
        )
        existing_link = link_result.scalar_one_or_none()

        if not existing_link:
            self.db.add(UserMedia(user_id=user_id, media_id=media_id))
            await self.db.commit()

    @staticmethod
    def extract_rutube_video_id(url: str) -> str:
        parsed = urlparse(url)
        host = parsed.netloc.lower()

        if "rutube.ru" not in host:
            raise HTTPException(status_code=400, detail="Only Rutube URLs are supported")

        patterns = [
            r"/video/([a-zA-Z0-9]+)/?",
            r"/play/embed/([a-zA-Z0-9]+)/?",
        ]

        for pattern in patterns:
            match = re.search(pattern, parsed.path)
            if match:
                return match.group(1)

        raise HTTPException(status_code=400, detail="Could not extract Rutube video id")

    @staticmethod
    def build_rutube_embed_url(video_id: str) -> str:
        return f"https://rutube.ru/play/embed/{video_id}"

    @staticmethod
    def _safe_filename(filename: str) -> str:
        filename = filename.strip() or "rutube_audio.mp3"
        filename = re.sub(r'[\\/:*?"<>|]+', "_", filename)
        return filename[:180]
