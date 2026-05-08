# modules/llm/tasks.py

import asyncio
import math
from datetime import datetime, UTC
from typing import Optional

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
from modules.llm.config import llm_config
from modules.llm.clients.gigachat import GigaChatClient
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
    Реальный LLM pipeline через GigaChat.

    Этапы:
    1. llm_map:
       MediaSegment -> LLM chunks -> SummaryChunk.summary_text

    2. llm_reduce:
       SummaryChunk.summary_text -> итоговый markdown-конспект -> Summary.content
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

        segments = (
            db.query(MediaSegment)
            .filter(MediaSegment.media_id == media.id)
            .order_by(MediaSegment.position)
            .all()
        )

        if not segments:
            raise ValueError("Media segments not found")

        summary.status = SummaryStatus.processing.value
        summary.error_message = None
        summary.content = None
        summary.content_json = None
        summary.title = None
        summary.provider = "gigachat"
        summary.model_name = llm_config.gigachat_model
        summary.prompt_version = "map-reduce-v1"
        db.commit()

        # На всякий случай очищаем старые чанки.
        db.query(SummaryChunk).filter(SummaryChunk.summary_id == summary.id).delete()
        db.commit()

        asyncio.run(
            run_gigachat_pipeline(
                db=db,
                task=self,
                job=job,
                summary=summary,
                media=media,
                segments=segments,
            )
        )

        return {
            "status": "SUCCESS",
            "job_id": str(job.id),
            "summary_id": str(summary.id),
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
                    meta={"message": "LLM task failed"},
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


async def run_gigachat_pipeline(
    db,
    task,
    job: ProcessingJob,
    summary: Summary,
    media: Media,
    segments: list[MediaSegment],
):
    client = GigaChatClient()

    chunks_data = build_llm_chunks(segments)

    if not chunks_data:
        raise ValueError("Could not build LLM chunks")

    # ============================================================
    # 1. LLM MAP
    # ============================================================
    update_stage(
        db=db,
        job_id=job.id,
        stage_name="llm_map",
        progress=0,
        status="processing",
        meta={
            "message": "Запуск анализа фрагментов",
            "total_chunks": len(chunks_data),
        },
    )

    system_prompt_map = build_map_system_prompt()

    for index, chunk_data in enumerate(chunks_data):
        chunk = SummaryChunk(
            summary_id=summary.id,
            job_id=job.id,
            position=index,
            start_time=chunk_data["start_time"],
            end_time=chunk_data["end_time"],
            source_text=chunk_data["text"],
            summary_text=None,
            status=SummaryChunkStatus.processing.value,
        )
        db.add(chunk)
        db.commit()
        db.refresh(chunk)

        prompt = build_map_prompt(
            chunk_index=index + 1,
            total_chunks=len(chunks_data),
            start_time=chunk.start_time,
            end_time=chunk.end_time,
            text=chunk.source_text,
        )

        map_result = await client.generate(
            prompt=prompt,
            system_prompt=system_prompt_map,
        )

        chunk.summary_text = normalize_markdown(map_result)
        chunk.status = SummaryChunkStatus.completed.value
        chunk.completed_at = utcnow()
        db.commit()

        processed = index + 1
        percent = int((processed / len(chunks_data)) * 100)

        update_stage(
            db=db,
            job_id=job.id,
            stage_name="llm_map",
            progress=percent,
            status="processing" if percent < 100 else "completed",
            meta={
                "message": "Фрагмент обработан",
                "processed_chunks": processed,
                "total_chunks": len(chunks_data),
            },
        )

        task.update_state(
            state="PROGRESS",
            meta={
                "stage": "llm_map",
                "percent": percent,
                "processed_chunks": processed,
                "total_chunks": len(chunks_data),
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
        meta={"message": "Сборка итогового конспекта"},
    )

    partial_chunks = (
        db.query(SummaryChunk)
        .filter(SummaryChunk.summary_id == summary.id)
        .order_by(SummaryChunk.position)
        .all()
    )

    update_stage(
        db=db,
        job_id=job.id,
        stage_name="llm_reduce",
        progress=30,
        status="processing",
        meta={"message": "Подготовка промежуточных summaries"},
    )

    reduce_prompt = build_reduce_prompt(partial_chunks)

    update_stage(
        db=db,
        job_id=job.id,
        stage_name="llm_reduce",
        progress=60,
        status="processing",
        meta={"message": "Генерация итогового markdown-конспекта"},
    )

    final_markdown = await client.generate(
        prompt=reduce_prompt,
        system_prompt=build_reduce_system_prompt(),
    )

    final_markdown = normalize_markdown(final_markdown)

    summary.title = extract_title(final_markdown) or "Интерактивный конспект"
    summary.content = final_markdown
    summary.status = SummaryStatus.completed.value
    summary.provider = "gigachat"
    summary.model_name = llm_config.gigachat_model
    summary.prompt_version = "map-reduce-v1"
    summary.completed_at = utcnow()
    summary.error_message = None

    db.commit()

    update_stage(
        db=db,
        job_id=job.id,
        stage_name="llm_reduce",
        progress=100,
        status="completed",
        meta={"message": "Конспект успешно создан"},
    )

    complete_job(db, job.id)


def build_llm_chunks(segments: list[MediaSegment]) -> list[dict]:
    """
    Собирает ASR-сегменты в LLM-чанки.

    Сейчас используется приближенная оценка:
    1 токен ≈ 4 символа.

    Это достаточно для первого рабочего варианта.
    Позже можно заменить на tokenizer конкретной модели.
    """
    max_chars = max(2000, llm_config.chunk_size_tokens * 4)
    max_chunks = llm_config.max_chunks_per_job

    prepared_segments = []

    for seg in segments:
        text = (seg.text or "").strip()

        if not text:
            continue

        prepared_segments.append({
            "start_time": seg.start_time,
            "end_time": seg.end_time,
            "text": f"[{format_seconds(seg.start_time)} — {format_seconds(seg.end_time)}]\n{text}",
        })

    if not prepared_segments:
        return []

    chunks = []
    current_texts = []
    current_start = None
    current_end = None
    current_len = 0

    for seg in prepared_segments:
        seg_text = seg["text"]
        seg_len = len(seg_text)

        if current_texts and current_len + seg_len > max_chars:
            chunks.append({
                "start_time": current_start,
                "end_time": current_end,
                "text": "\n\n".join(current_texts),
            })

            current_texts = []
            current_start = None
            current_end = None
            current_len = 0

        if current_start is None:
            current_start = seg["start_time"]

        current_end = seg["end_time"]
        current_texts.append(seg_text)
        current_len += seg_len

    if current_texts:
        chunks.append({
            "start_time": current_start,
            "end_time": current_end,
            "text": "\n\n".join(current_texts),
        })

    if len(chunks) <= max_chunks:
        return chunks

    return rebuild_chunks_by_count(prepared_segments, max_chunks)


def rebuild_chunks_by_count(prepared_segments: list[dict], max_chunks: int) -> list[dict]:
    """
    Если чанков получилось слишком много, переразбиваем сегменты
    примерно равномерно на max_chunks частей.
    """
    if max_chunks <= 0:
        raise ValueError("max_chunks_per_job must be greater than zero")

    chunk_size = math.ceil(len(prepared_segments) / max_chunks)
    chunks = []

    for i in range(0, len(prepared_segments), chunk_size):
        group = prepared_segments[i:i + chunk_size]

        chunks.append({
            "start_time": group[0]["start_time"],
            "end_time": group[-1]["end_time"],
            "text": "\n\n".join(item["text"] for item in group),
        })

    return chunks


def build_map_system_prompt() -> str:
    return (
        "Ты — помощник для создания учебных конспектов по транскриптам лекций, "
        "вебинаров и подкастов. "
        "Твоя задача — аккуратно анализировать фрагмент транскрипта, "
        "не выдумывать факты и сохранять связь с временными метками."
    )


def build_map_prompt(
    chunk_index: int,
    total_chunks: int,
    start_time: Optional[float],
    end_time: Optional[float],
    text: str,
) -> str:
    return f"""
Проанализируй фрагмент транскрипта.

Фрагмент: {chunk_index} из {total_chunks}
Временной интервал: {format_seconds(start_time)} — {format_seconds(end_time)}

Требования:
- Пиши на русском языке.
- Не выдумывай факты, которых нет в транскрипте.
- Сохраняй важные временные метки.
- Убери речевой шум, повторы и случайные оговорки.
- Сформулируй кратко, но содержательно.
- Используй markdown.

Структура ответа:

### Фрагмент {chunk_index}: {format_seconds(start_time)} — {format_seconds(end_time)}

#### Ключевые идеи
- ...

#### Важные детали
- ...

#### Возможные термины и понятия
- ...

#### Таймкоды
- `MM:SS` — что происходит в этот момент

Транскрипт фрагмента:

{text}
""".strip()


def build_reduce_system_prompt() -> str:
    return (
        "Ты — редактор учебных материалов. "
        "Ты собираешь финальный интерактивный конспект из промежуточных summaries. "
        "Нужно получить чистый markdown-документ, пригодный для чтения студентом."
    )


def build_reduce_prompt(chunks: list[SummaryChunk]) -> str:
    partials = []

    for chunk in chunks:
        partials.append(
            f"""
Фрагмент {chunk.position + 1}
Интервал: {format_seconds(chunk.start_time)} — {format_seconds(chunk.end_time)}

{chunk.summary_text or ""}
""".strip()
        )

    joined_partials = "\n\n---\n\n".join(partials)

    return f"""
Собери единый структурированный интерактивный конспект на основе промежуточных summaries.

Требования:
- Ответ должен быть на русском языке.
- Формат ответа — только markdown.
- Не добавляй факты, которых нет в промежуточных summaries.
- Сохраняй временные метки там, где они полезны.
- Конспект должен быть понятен человеку, который не смотрел исходное видео.
- Не упоминай, что ты LLM или что текст был обработан моделью.
- Не добавляй служебные комментарии.

Структура итогового markdown:

# Название темы

## Краткое содержание

2–5 абзацев общего содержания.

## Основные темы

- Тема 1
- Тема 2
- Тема 3

## Подробный конспект

### Название смыслового раздела
Краткое объяснение.

- `MM:SS` — важный момент
- `MM:SS` — важный момент

### Следующий смысловой раздел
...

## Ключевые понятия

- **Понятие** — объяснение простыми словами.

## Вопросы для самопроверки

1. Вопрос?
2. Вопрос?
3. Вопрос?

## Итог

Краткий вывод по материалу.

Промежуточные summaries:

{joined_partials}
""".strip()


def normalize_markdown(text: str) -> str:
    text = (text or "").strip()

    # Иногда модели оборачивают markdown в ```markdown ... ```
    if text.startswith("```markdown"):
        text = text.removeprefix("```markdown").strip()

    if text.startswith("```"):
        text = text.removeprefix("```").strip()

    if text.endswith("```"):
        text = text.removesuffix("```").strip()

    return text


def extract_title(markdown: str) -> Optional[str]:
    for line in markdown.splitlines():
        line = line.strip()

        if line.startswith("# "):
            return line.replace("# ", "", 1).strip()

    return None


def format_seconds(seconds: Optional[float]) -> str:
    if seconds is None:
        return "00:00"

    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    return f"{minutes:02d}:{secs:02d}"
