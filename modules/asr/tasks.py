import os
import uuid
from datetime import datetime, UTC
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from modules.asr.config import asr_config
from modules.asr.service import transcribe_segments, split_audio, load_model
from modules.auth.models import User
from modules.media.models import Media, ProcessingJob, Transcription
from modules.shared.database import db_settings
from modules.shared.celery import celery_app
from celery.signals import worker_ready
import librosa

# Синхронный engine для Celery
SYNC_DATABASE_URL = db_settings.database_url.replace("+asyncpg", "")
sync_engine = create_engine(SYNC_DATABASE_URL)
SyncSession = sessionmaker(bind=sync_engine)


@celery_app.task
def process_asr(media_id: str):
    session = SyncSession()
    temp_audio_path = None  # для возможной очистки
    try:
        # Находим запись Media
        media = session.get(Media, uuid.UUID(media_id))
        if not media:
            print(f"Media {media_id} not found")
            return

        # Находим соответствующую задачу ProcessingJob для этапа asr
        job = session.execute(
            select(ProcessingJob).where(
                ProcessingJob.media_id == media_id,
                ProcessingJob.stage == "asr"
            )
        ).scalar_one_or_none()

        if not job:
            print(f"ProcessingJob for media {media_id} not found")
            return

        # Обновляем статус задачи
        job.status = "processing"
        job.started_at = datetime.now(UTC)
        session.commit()

        # 1. Загружаем аудио из файла (librosa сама извлечёт из видео, если надо)
        print(f"Загрузка аудио из {media.file_path}")
        audio, sr = librosa.load(media.file_path, sr=asr_config.sample_rate, mono=True)

        # 2. Разбиваем на технические сегменты с перекрытием
        segments = split_audio(
            audio, sr,
            segment_length=asr_config.segment_length,
            overlap=asr_config.overlap_seconds
        )
        # segments - список кортежей (start, end)

        # 3. Транскрибируем сегменты
        all_segments = transcribe_segments(
            audio, sr, segments,
            temp_dir=asr_config.temp_dir
        )

        # 4. Сортируем по времени начала
        all_segments.sort(key=lambda x: x['start'])

        # 5. Простая дедупликация перекрывающихся сегментов (можно улучшить)
        merged = []
        prev = None
        for seg in all_segments:
            if prev and seg['start'] < prev['end'] + 0.5:
                # если текст похож, пропускаем
                if seg['text'] in prev['text'] or prev['text'] in seg['text']:
                    continue
            merged.append(seg)
            prev = seg

        # 6. Полный текст
        full_text = " ".join([s['text'] for s in merged])

        # 7. Сохраняем транскрипцию
        trans = Transcription(
            id=uuid.uuid4(),
            media_id=media.id,
            segments=merged,  # JSONB
            full_text=full_text,
            model_name=asr_config.model_name
        )
        session.add(trans)

        # 8. Обновляем статус Media
        media.status = "transcribed"
        media.processing_stage = None
        media.updated_at = datetime.now(UTC)

        # 9. Обновляем задачу
        job.status = "completed"
        job.completed_at = datetime.now(UTC)
        job.duration_seconds = (job.completed_at - job.started_at).total_seconds()
        session.commit()

        print(f"ASR completed for media {media_id}, {len(merged)} segments")

        # TODO: запустить LLM задачу
        # from modules.llm.tasks import process_llm
        # process_llm.delay(media_id)

    except Exception as e:
        session.rollback()
        if 'job' in locals() and job:
            job.status = "failed"
            job.error_message = str(e)
            session.commit()
        print(f"ASR failed for media {media_id}: {e}")
        raise
    finally:
        # Очистка временных файлов (transcribe_segments уже удаляет свои, но на всякий случай)
        if temp_audio_path and os.path.exists(temp_audio_path):
            os.remove(temp_audio_path)
        session.close()


@worker_ready.connect
def on_worker_ready(**kwargs):
    """Загружаем модель при старте воркера (в фоне, чтобы не блокировать)"""
    load_model()
    print("ASR модель загружена в память воркера")
