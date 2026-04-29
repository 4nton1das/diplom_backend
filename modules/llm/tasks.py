# modules/llm/tasks.py

import time
from datetime import datetime, UTC

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from modules.shared.celery import celery_app
from modules.shared.processing import update_stage, complete_job, fail_job
from modules.media.config import media_config
from modules.media.models import (
    Media,
    MediaSegment,
    ProcessingJob,
)
from modules.llm.models import (
    Summary,
    SummaryChunk,
    SummaryStatus,
    SummaryChunkStatus,
)


SYNC_DATABASE_URL = media_config.database_url.replace(
    "postgresql+asyncpg",
    "postgresql"
)

engine = create_engine(SYNC_DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def utcnow():
    return datetime.now(UTC)


@celery_app.task(bind=True, name="llm.process_summary")
def process_summary_task(self, job_id_str: str, summary_id_str: str):
    """
    Временная mock LLM-задача.

    Нужна сейчас для проверки:
    - ProcessingJob(job_type='summary')
    - ProcessingStage(llm_map)
    - ProcessingStage(llm_reduce)
    - SummaryChunk
    - frontend progress UI
    - websocket/polling

    Позже внутри этой задачи заменим mock-генерацию на реальный map-reduce LLM.
    """
    db = SessionLocal()

    job = None
    summary = None

    try:
        job = (
            db.query(ProcessingJob)
            .filter(
                ProcessingJob.id == job_id_str,
                ProcessingJob.job_type == "summary",
            )
            .first()
        )

        if not job:
            raise ValueError(f"Summary ProcessingJob not found: {job_id_str}")

        summary = (
            db.query(Summary)
            .filter(
                Summary.id == summary_id_str,
                Summary.job_id == job.id,
            )
            .first()
        )

        if not summary:
            raise ValueError(f"Summary not found: {summary_id_str}")

        media = db.query(Media).filter(Media.id == job.media_id).first()

        if not media:
            raise ValueError(f"Media not found: {job.media_id}")

        if not media.full_text:
            raise ValueError("Media transcript is empty")

        summary.status = SummaryStatus.processing.value
        db.commit()

        # Удаляем старые чанки на случай повторного запуска этой же job.
        db.query(SummaryChunk).filter(SummaryChunk.summary_id == summary.id).delete()
        db.commit()

        segments = (
            db.query(MediaSegment)
            .filter(MediaSegment.media_id == media.id)
            .order_by(MediaSegment.position)
            .all()
        )

        if not segments:
            raise ValueError("Media segments not found")

        # ============================================================
        # 1. LLM MAP
        # ============================================================
        update_stage(
            db=db,
            job_id=job.id,
            stage_name="llm_map",
            progress=0,
            status="processing",
            meta={"message": "Starting mock LLM map stage"},
        )

        # Сейчас делаем группы по 5 ASR-сегментов.
        # Позже здесь будет token-based chunking.
        group_size = 5
        groups = [
            segments[i:i + group_size]
            for i in range(0, len(segments), group_size)
        ]

        chunks: list[SummaryChunk] = []

        for index, group in enumerate(groups):
            source_text = " ".join(seg.text or "" for seg in group).strip()

            start_time = group[0].start_time
            end_time = group[-1].end_time

            chunk = SummaryChunk(
                summary_id=summary.id,
                job_id=job.id,
                position=index,
                start_time=start_time,
                end_time=end_time,
                source_text=source_text,
                summary_text=None,
                status=SummaryChunkStatus.processing.value,
            )
            db.add(chunk)
            db.commit()
            db.refresh(chunk)

            # Имитация вызова LLM.
            time.sleep(1)

            chunk.summary_text = (
                f"### Фрагмент {index + 1}\n\n"
                f"- Временной интервал: {format_seconds(start_time)} — {format_seconds(end_time)}.\n"
                f"- Краткое содержание фрагмента: {make_short_mock_summary(source_text)}\n"
            )
            chunk.status = SummaryChunkStatus.completed.value
            chunk.completed_at = utcnow()

            db.commit()

            chunks.append(chunk)

            percent = int(((index + 1) / len(groups)) * 100)

            update_stage(
                db=db,
                job_id=job.id,
                stage_name="llm_map",
                progress=percent,
                status="processing" if percent < 100 else "completed",
                meta={
                    "message": "Mock LLM map chunk processed",
                    "processed_chunks": index + 1,
                    "total_chunks": len(groups),
                },
            )

            self.update_state(
                state="PROGRESS",
                meta={
                    "stage": "llm_map",
                    "percent": percent,
                    "processed_chunks": index + 1,
                    "total_chunks": len(groups),
                },
            )

        # ============================================================
        # 2. LLM REDUCE
        # ============================================================
        update_stage(
            db=db,
            job_id=job.id,
            stage_name="llm_reduce",
            progress=0,
            status="processing",
            meta={"message": "Starting mock LLM reduce stage"},
        )

        time.sleep(1)
        update_stage(
            db=db,
            job_id=job.id,
            stage_name="llm_reduce",
            progress=40,
            status="processing",
            meta={"message": "Collecting partial summaries"},
        )

        partials = (
            db.query(SummaryChunk)
            .filter(SummaryChunk.summary_id == summary.id)
            .order_by(SummaryChunk.position)
            .all()
        )

        time.sleep(1)
        update_stage(
            db=db,
            job_id=job.id,
            stage_name="llm_reduce",
            progress=75,
            status="processing",
            meta={"message": "Building final markdown summary"},
        )

        final_markdown = build_mock_markdown_summary(
            media=media,
            chunks=partials,
        )

        summary.title = "Черновой интерактивный конспект"
        summary.content = final_markdown
        summary.status = SummaryStatus.completed.value
        summary.provider = "mock"
        summary.model_name = "mock-llm"
        summary.prompt_version = "mock-v1"
        summary.completed_at = utcnow()

        db.commit()

        update_stage(
            db=db,
            job_id=job.id,
            stage_name="llm_reduce",
            progress=100,
            status="completed",
            meta={"message": "Mock summary completed"},
        )

        complete_job(db, job.id)

        return {
            "status": "SUCCESS",
            "job_id": str(job.id),
            "summary_id": str(summary.id),
            "chunks": len(partials),
        }

    except Exception as e:
        db.rollback()

        error_message = str(e)

        if job:
            try:
                current_stage = job.current_stage or "llm_map"
                update_stage(
                    db=db,
                    job_id=job.id,
                    stage_name=current_stage,
                    progress=job.progress or 0,
                    status="failed",
                    error_message=error_message,
                    meta={"message": "Mock LLM task failed"},
                )
                fail_job(db, job.id, error_message)
            except Exception:
                db.rollback()

        if summary:
            try:
                summary.status = SummaryStatus.failed.value
                summary.error_message = error_message
                db.commit()
            except Exception:
                db.rollback()

        raise

    finally:
        db.close()


def format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "00:00"

    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    return f"{minutes:02d}:{secs:02d}"


def make_short_mock_summary(text: str) -> str:
    if not text:
        return "Фрагмент не содержит распознанного текста."

    words = text.split()
    preview = " ".join(words[:35])

    if len(words) > 35:
        preview += "..."

    return preview


def build_mock_markdown_summary(media: Media, chunks: list[SummaryChunk]) -> str:
    parts = [
        "# Интерактивный конспект",
        "",
        "> Это временный mock-конспект. Позже он будет заменён результатом LLM map-reduce.",
        "",
        "## Краткое содержание",
        "",
        "Материал был автоматически транскрибирован и разбит на смысловые фрагменты. "
        "Ниже приведены промежуточные summaries по временным интервалам.",
        "",
        "## Фрагменты с временными метками",
        "",
    ]

    for chunk in chunks:
        parts.append(
            f"### {format_seconds(chunk.start_time)} — {format_seconds(chunk.end_time)}"
        )
        parts.append("")
        parts.append(chunk.summary_text or "Нет данных.")
        parts.append("")

    parts.extend([
        "## Общий вывод",
        "",
        "Это демонстрационный markdown-конспект для проверки интерфейса, статусов и пайплайна обработки.",
        "",
    ])

    return "\n".join(parts)
