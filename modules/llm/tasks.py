# modules/llm/tasks.py
import uuid
from datetime import datetime, UTC
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from modules.shared.database import db_settings
from modules.shared.celery import celery_app
from modules.media.models import Media, ProcessingJob
from modules.llm.config import llm_config

# Синхронный engine для Celery
SYNC_DATABASE_URL = db_settings.database_url.replace("+asyncpg", "")
sync_engine = create_engine(SYNC_DATABASE_URL)
SyncSession = sessionmaker(bind=sync_engine)


@celery_app.task(bind=True, max_retries=3)
def process_llm(self, media_id: str):
    """Celery задача для генерации конспекта"""
    session = SyncSession()

    try:
        # Находим запись Media
        media = session.get(Media, uuid.UUID(media_id))
        if not media:
            print(f"Media {media_id} not found")
            return

        # Находим задачу ProcessingJob для этапа llm
        job = session.execute(
            select(ProcessingJob).where(
                ProcessingJob.media_id == media_id,
                ProcessingJob.stage == "llm"
            )
        ).scalar_one_or_none()

        if not job:
            # Создаём новую задачу
            job = ProcessingJob(
                id=uuid.uuid4(),
                media_id=media_id,
                stage="llm",
                status="pending"
            )
            session.add(job)
            session.commit()

        # Обновляем статус задачи
        job.status = "processing"
        job.started_at = datetime.now(UTC)
        session.commit()

        # Импортируем сервис (внутри функции для избежания circular imports)
        from modules.llm.service import LLMService
        from modules.shared.database import AsyncSessionLocal
        import asyncio

        # Запускаем async код в sync контексте
        async def run_llm():
            async with AsyncSessionLocal() as db_session:
                service = LLMService(db_session)
                return await service.generate_summary(media_id)

        summary = asyncio.run(run_llm())

        # Обновляем задачу
        job.status = "completed"
        job.completed_at = datetime.now(UTC)
        job.duration_seconds = (job.completed_at - job.started_at).total_seconds()
        session.commit()

        print(f"LLM completed for media {media_id}")

    except Exception as e:
        session.rollback()

        if 'job' in locals() and job:
            job.status = "failed"
            job.error_message = str(e)
            session.commit()

        print(f"LLM failed for media {media_id}: {e}")

        # Retry logic
        raise self.retry(exc=e, countdown=llm_config.retry_delay_seconds)

    finally:
        session.close()
