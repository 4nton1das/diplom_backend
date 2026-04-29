# modules/asr/tasks.py
import os
import shutil
import tempfile
import bentoml
from pydub import AudioSegment
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from modules.shared.celery import celery_app
from modules.shared.processing import update_stage, complete_job, fail_job
from modules.media.config import media_config
from modules.media.models import (
    Media,
    MediaSegment,
    MediaStatus,
    ProcessingJob,
)
from modules.media.storage import s3_storage

SYNC_DATABASE_URL = media_config.database_url.replace("postgresql+asyncpg", "postgresql")

engine = create_engine(SYNC_DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

asr_client = bentoml.SyncHTTPClient("http://localhost:3000", timeout=600)


@celery_app.task(bind=True, name="asr.process_media")
def process_media_task(self, media_id_str: str, job_id_str: str | None = None):
    """
    ASR pipeline:
    1. preparing      — скачивание из S3, конвертация, нарезка
    2. transcribing   — батчевая отправка чанков в ASR
    3. finalizing     — сборка full_text

    Главный источник прогресса — таблицы:
    - media.processing_jobs
    - media.processing_stages
    """
    db = SessionLocal()
    tmp_dir = tempfile.mkdtemp()

    STEP_SEC = 40.0
    OVERLAP_SEC = 2.0
    BATCH_SIZE = 8

    media = None
    job = None

    try:
        media = db.query(Media).filter(Media.id == media_id_str).first()
        if not media:
            raise ValueError(f"Media not found: {media_id_str}")

        if job_id_str:
            job = (
                db.query(ProcessingJob)
                .filter(
                    ProcessingJob.id == job_id_str,
                    ProcessingJob.media_id == media.id,
                    ProcessingJob.job_type == "asr",
                )
                .first()
            )
        else:
            job = (
                db.query(ProcessingJob)
                .filter(
                    ProcessingJob.media_id == media.id,
                    ProcessingJob.job_type == "asr",
                )
                .order_by(ProcessingJob.created_at.desc())
                .first()
            )

        if not job:
            raise ValueError(f"ASR ProcessingJob not found for media_id={media_id_str}")

        # На случай повторного запуска ASR очищаем старые сегменты.
        db.query(MediaSegment).filter(MediaSegment.media_id == media.id).delete()
        db.commit()

        # ============================================================
        # 1. PREPARING
        # ============================================================
        media.status = MediaStatus.PREPARING
        db.commit()

        update_stage(
            db=db,
            job_id=job.id,
            stage_name="preparing",
            progress=0,
            status="processing",
            meta={"message": "Downloading and preparing audio"},
        )

        raw_file = os.path.join(tmp_dir, "raw_input")
        mono_file = os.path.join(tmp_dir, "mono_input.wav")

        s3_storage.download_file(media.s3_key, raw_file)

        update_stage(
            db=db,
            job_id=job.id,
            stage_name="preparing",
            progress=30,
            status="processing",
            meta={"message": "Audio downloaded from S3"},
        )

        audio = AudioSegment.from_file(raw_file)
        audio = audio.set_channels(1).set_frame_rate(16000)
        audio.export(mono_file, format="wav")

        duration_sec = len(audio) / 1000.0

        update_stage(
            db=db,
            job_id=job.id,
            stage_name="preparing",
            progress=60,
            status="processing",
            meta={
                "message": "Audio converted to mono 16kHz wav",
                "duration_sec": duration_sec,
            },
        )

        current_start = 0.0
        idx = 0

        while current_start < duration_sec:
            start_t = current_start
            end_t = min(start_t + STEP_SEC, duration_sec)
            actual_end_t = min(end_t + OVERLAP_SEC, duration_sec)

            chunk = audio[int(start_t * 1000): int(actual_end_t * 1000)]
            chunk_path = os.path.join(tmp_dir, f"chunk_{idx}.wav")
            chunk.export(chunk_path, format="wav")

            db.add(
                MediaSegment(
                    media_id=media.id,
                    position=idx,
                    start_time=start_t,
                    end_time=end_t,
                    text="",
                    words=[],
                )
            )

            current_start += STEP_SEC
            idx += 1

        db.commit()

        update_stage(
            db=db,
            job_id=job.id,
            stage_name="preparing",
            progress=100,
            status="completed",
            meta={
                "message": "Audio sliced into chunks",
                "segments_count": idx,
            },
        )

        # ============================================================
        # 2. TRANSCRIBING
        # ============================================================
        media.status = MediaStatus.TRANSCRIBING
        db.commit()

        update_stage(
            db=db,
            job_id=job.id,
            stage_name="transcribing",
            progress=0,
            status="processing",
            meta={"message": "Starting ASR transcription"},
        )

        all_segments = (
            db.query(MediaSegment)
            .filter(MediaSegment.media_id == media.id)
            .order_by(MediaSegment.position)
            .all()
        )

        if not all_segments:
            raise ValueError("No MediaSegment rows created")

        global_words = []
        total_segments = len(all_segments)

        for j in range(0, total_segments, BATCH_SIZE):
            batch = all_segments[j:j + BATCH_SIZE]
            paths = [
                os.path.join(tmp_dir, f"chunk_{seg.position}.wav")
                for seg in batch
            ]

            results = asr_client.call("transcribe", paths=paths)

            for seg, res in zip(batch, results):
                seg_offset = seg.start_time or 0.0
                segment_valid_words = []

                for w in res.get("words", []):
                    abs_start = float(w["start"]) + seg_offset
                    abs_end = float(w["end"]) + seg_offset

                    # Берем слово только если оно началось внутри логического сегмента.
                    # Слова из overlap-зоны будут обработаны следующим сегментом.
                    if seg.start_time <= abs_start < seg.end_time:
                        word_obj = {
                            "word": w["word"],
                            "start": round(abs_start, 2),
                            "end": round(abs_end, 2),
                        }
                        segment_valid_words.append(word_obj)
                        global_words.append(word_obj)

                seg.words = segment_valid_words
                seg.text = " ".join(w["word"] for w in segment_valid_words)

            db.commit()

            processed_segments = min(j + len(batch), total_segments)
            percent = int((processed_segments / total_segments) * 100)

            update_stage(
                db=db,
                job_id=job.id,
                stage_name="transcribing",
                progress=percent,
                status="processing" if percent < 100 else "completed",
                meta={
                    "message": "ASR batch processed",
                    "processed_segments": processed_segments,
                    "total_segments": total_segments,
                },
            )

            # Можно оставить для Flower/Celery debug, но UI должен читать БД.
            self.update_state(
                state="PROGRESS",
                meta={
                    "stage": "transcribing",
                    "percent": percent,
                    "processed_segments": processed_segments,
                    "total_segments": total_segments,
                },
            )

        # ============================================================
        # 3. FINALIZING
        # ============================================================
        update_stage(
            db=db,
            job_id=job.id,
            stage_name="finalizing",
            progress=0,
            status="processing",
            meta={"message": "Building final transcript"},
        )

        global_words.sort(key=lambda x: x["start"])

        media.full_text = " ".join(w["word"] for w in global_words)
        media.status = MediaStatus.COMPLETED
        db.commit()

        update_stage(
            db=db,
            job_id=job.id,
            stage_name="finalizing",
            progress=100,
            status="completed",
            meta={
                "message": "Transcript completed",
                "words_count": len(global_words),
            },
        )

        complete_job(db, job.id)

        return {
            "status": "SUCCESS",
            "media_id": str(media.id),
            "job_id": str(job.id),
            "segments": total_segments,
            "words": len(global_words),
        }

    except Exception as e:
        db.rollback()

        error_message = str(e)

        if job:
            try:
                current_stage = job.current_stage or "unknown"
                update_stage(
                    db=db,
                    job_id=job.id,
                    stage_name=current_stage,
                    progress=job.progress or 0,
                    status="failed",
                    error_message=error_message,
                    meta={"message": "ASR task failed"},
                )
                fail_job(db, job.id, error_message)
            except Exception:
                db.rollback()

        if media:
            try:
                media.status = MediaStatus.FAILED
                db.commit()
            except Exception:
                db.rollback()

        raise

    finally:
        db.close()
        shutil.rmtree(tmp_dir, ignore_errors=True)
