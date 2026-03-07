# modules/media/tasks.py
import time
import uuid
from datetime import datetime
from celery import Celery
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from modules.media.config import media_config
from modules.media.models import Media, ProcessingJob

# Синхронный engine (заменяем asyncpg на обычный postgresql)
SYNC_DATABASE_URL = media_config.database_url.replace('+asyncpg', '')  # предполагаем, что в media_config есть database_url
# Если нет, добавь в media_config поле database_url, читаемое из .env

engine = create_engine(SYNC_DATABASE_URL)
Session = sessionmaker(bind=engine)

celery_app = Celery(
    "media_tasks",
    broker=media_config.celery_broker_url,
    backend=media_config.celery_result_backend
)


@celery_app.task
def process_asr(media_id: str):
    session = Session()
    try:
        # Находим задачу
        job = session.execute(
            select(ProcessingJob).where(
                ProcessingJob.media_id == media_id,
                ProcessingJob.stage == "asr"
            )
        ).scalar_one_or_none()
        if not job:
            return

        # Меняем статус на processing
        job.status = "processing"
        job.started_at = datetime.now()
        session.commit()

        # TODO: здесь реальный вызов ASR
        # Например, вызов внешнего API или запуск локальной модели
        # Пока просто имитация
        time.sleep(20)  # имитация работы

        # Обновляем задачу
        job.status = "completed"
        job.completed_at = datetime.now()
        job.duration_seconds = 5
        session.commit()

        # Обновляем статус медиа
        session.execute(
            update(Media)
            .where(Media.id == media_id)
            .values(status="transcribed", processing_stage=None)
        )
        session.commit()

    except Exception as e:
        session.rollback()
        if job:
            job.status = "failed"
            job.error_message = str(e)
            session.commit()
        raise
    finally:
        session.close()

    return {"status": "completed", "media_id": media_id}
