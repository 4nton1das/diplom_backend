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
from modules.llm.structured import (
    StructuredChunkSummary,
    StructuredSection,
    extract_json_object,
    parse_structured_chunk_summary,
    merge_chunk_summaries,
    structured_chunk_to_markdown,
    structured_summary_to_markdown,
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

    chunks_data = build_llm_chunks_by_time(segments, chunk_duration_sec=300)

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

    system_prompt_map = build_chunk_json_map_system_prompt()
    mapped_chunks: list[StructuredChunkSummary] = []

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

        prompt = build_chunk_json_map_prompt(
            chunk_index=index + 1,
            total_chunks=len(chunks_data),
            start_time=chunk.start_time,
            end_time=chunk.end_time,
            text=chunk.source_text,
        )

        structured_chunk = await generate_chunk_json_with_retry(
            client=client,
            prompt=prompt,
            system_prompt=system_prompt_map,
            chunk_index=index + 1,
            chunk_start=float(chunk.start_time or 0),
            chunk_end=float(chunk.end_time or chunk.start_time or 0),
            source_text=chunk.source_text,
        )

        chunk.summary_json = structured_chunk.model_dump(mode="json")
        chunk.summary_text = structured_chunk_to_markdown(structured_chunk)
        chunk.status = SummaryChunkStatus.completed.value
        chunk.completed_at = utcnow()
        db.commit()

        mapped_chunks.append(structured_chunk)

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

    stored_chunks = (
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
        meta={"message": "Сборка структурированных фрагментов"},
    )

    mapped_chunks = [
        StructuredChunkSummary.model_validate(chunk.summary_json)
        for chunk in stored_chunks
        if chunk.summary_json
    ]

    if not mapped_chunks:
        raise ValueError("No structured map chunks found")

    update_stage(
        db=db,
        job_id=job.id,
        stage_name="llm_reduce",
        progress=55,
        status="processing",
        meta={"message": "Генерация общего названия и обзора"},
    )

    metadata = await generate_summary_metadata(client, mapped_chunks)

    update_stage(
        db=db,
        job_id=job.id,
        stage_name="llm_reduce",
        progress=75,
        status="processing",
        meta={"message": "Объединение разделов без потери структуры"},
    )

    structured_summary = merge_chunk_summaries(
        mapped_chunks,
        title=metadata.get("title"),
        overview=metadata.get("overview"),
    )

    final_markdown = structured_summary_to_markdown(structured_summary)

    summary.title = structured_summary.title
    summary.content = final_markdown
    summary.content_json = structured_summary.model_dump(mode="json")
    summary.status = SummaryStatus.completed.value
    summary.provider = "gigachat"
    summary.model_name = llm_config.gigachat_model
    summary.prompt_version = "chunk-json-map-v1"
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


def build_structured_reduce_system_prompt() -> str:
    return (
        "Ты — редактор учебных материалов. "
        "Твоя задача — собрать структурированный интерактивный конспект "
        "из промежуточных summaries. "
        "Ответ должен быть строго валидным JSON-объектом. "
        "Не используй markdown. "
        "Не добавляй пояснения до или после JSON. "
        "Не оборачивай JSON в ```."
    )


def build_structured_reduce_prompt(chunks: list[SummaryChunk]) -> str:
    partials = []

    for chunk in chunks:
        partials.append(
            f"""
Фрагмент {chunk.position + 1}
Интервал: {format_seconds(chunk.start_time)} — {format_seconds(chunk.end_time)}
start_time_seconds: {safe_float(chunk.start_time)}
end_time_seconds: {safe_float(chunk.end_time)}

{chunk.summary_text or ""}
""".strip()
        )

    joined_partials = "\n\n---\n\n".join(partials)

    return f"""
Собери единый структурированный интерактивный конспект на основе промежуточных summaries.

Верни строго JSON-объект следующего формата:

{{
  "title": "Короткое название темы",
  "overview": "Краткое содержание материала в 2-5 предложениях",
  "sections": [
    {{
      "id": "section_1",
      "title": "Название смыслового раздела",
      "start_time": 0.0,
      "end_time": 120.0,
      "summary": "Краткое объяснение раздела",
      "points": [
        {{
          "time": 15.5,
          "text": "Ключевой тезис, привязанный ко времени"
        }}
      ]
    }}
  ],
  "terms": [
    {{
      "term": "Термин",
      "definition": "Понятное определение термина",
      "time": 42.0
    }}
  ],
  "questions": [
    {{
      "question": "Вопрос для самопроверки",
      "answer": "Краткий ответ на вопрос",
      "time": 80.0
    }}
  ]
}}

Жёсткие правила:
- Ответ должен быть только JSON.
- Не используй markdown.
- Не добавляй текст до или после JSON.
- Все ключи должны быть такими же, как в примере.
- Временные значения указывай числами в секундах, не строками.
- Если точное время неизвестно, используй ближайшее доступное время из фрагмента.
- Не выдумывай факты, которых нет в summaries.
- Разделы должны идти в хронологическом порядке.
- points должны быть конкретными тезисами, а не общими фразами.
- terms должны содержать только реально важные понятия из материала.
- questions должны проверять понимание материала.

Промежуточные summaries:

{joined_partials}
""".strip()


def safe_float(value):
    if value is None:
        return "null"

    return round(float(value), 2)


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


def build_llm_chunks_by_time(
    segments: list[MediaSegment],
    chunk_duration_sec: int = 300,
) -> list[dict]:
    """
    Собирает ASR-сегменты в LLM-чанки по длительности.

    300 секунд = 5 минут.
    Для часового видео получится примерно 12 чанков.
    """
    prepared = []

    for seg in segments:
        text = (seg.text or "").strip()

        if not text:
            continue

        prepared.append({
            "start_time": float(seg.start_time or 0),
            "end_time": float(seg.end_time or seg.start_time or 0),
            "text": f"[{format_seconds(seg.start_time)} — {format_seconds(seg.end_time)}]\n{text}",
        })

    if not prepared:
        return []

    chunks = []
    current = []
    current_start = prepared[0]["start_time"]
    current_end = prepared[0]["end_time"]

    for item in prepared:
        if current and item["start_time"] >= current_start + chunk_duration_sec:
            chunks.append({
                "start_time": current_start,
                "end_time": current_end,
                "text": "\n\n".join(x["text"] for x in current),
            })

            current = []
            current_start = item["start_time"]

        current.append(item)
        current_end = item["end_time"]

    if current:
        chunks.append({
            "start_time": current_start,
            "end_time": current_end,
            "text": "\n\n".join(x["text"] for x in current),
        })

    return chunks


def build_chunk_json_map_system_prompt() -> str:
    return (
        "Ты анализируешь фрагмент транскрипта лекции. "
        "Твоя задача — вернуть структурированный JSON только по этому фрагменту. "
        "Не пересказывай весь материал целиком. "
        "Не используй markdown. "
        "Не добавляй пояснения до или после JSON. "
        "Все временные метки должны быть только внутри заданного интервала."
    )


def build_chunk_json_map_prompt(
    chunk_index: int,
    total_chunks: int,
    start_time: float,
    end_time: float,
    text: str,
) -> str:
    return f"""
Проанализируй только этот фрагмент транскрипта.

Фрагмент: {chunk_index} из {total_chunks}
Интервал фрагмента в секундах: {round(float(start_time), 2)} — {round(float(end_time), 2)}
Интервал фрагмента во времени: {format_seconds(start_time)} — {format_seconds(end_time)}

Верни строго JSON-объект такого формата:

{{
  "sections": [
    {{
      "title": "Название смыслового раздела внутри этого фрагмента",
      "start_time": {round(float(start_time), 2)},
      "end_time": {round(float(end_time), 2)},
      "summary": "Краткое, но содержательное объяснение раздела",
      "points": [
        {{
          "time": {round(float(start_time), 2)},
          "text": "Конкретный тезис из этого фрагмента"
        }}
      ]
    }}
  ],
  "terms": [
    {{
      "term": "Термин",
      "definition": "Определение на основе фрагмента",
      "time": {round(float(start_time), 2)}
    }}
  ],
  "questions": [
    {{
      "question": "Вопрос для самопроверки по этому фрагменту",
      "answer": "Краткий ответ",
      "time": {round(float(start_time), 2)}
    }}
  ]
}}

Правила:
- Ответ должен быть только JSON.
- Не используй markdown.
- Не оборачивай JSON в ```json.
- Не добавляй текст до или после JSON.
- Все start_time, end_time и time должны быть числами в секундах.
- Запрещено использовать время меньше {round(float(start_time), 2)}.
- Запрещено использовать время больше {round(float(end_time), 2)}.
- Используй временные метки из транскрипта вида [MM:SS — MM:SS].
- Если точное время тезиса неизвестно, используй начало ближайшего сегмента.
- Не выдумывай факты.
- Для 5-минутного фрагмента обычно нужно 1–3 sections.
- В каждом section желательно 2–5 points.
- terms и questions могут быть пустыми массивами, если в фрагменте нет важных терминов или вопросов.

Транскрипт фрагмента:

{text}
""".strip()


async def generate_summary_metadata(
    client: GigaChatClient,
    chunks: list[StructuredChunkSummary],
) -> dict:
    """
    LLM используется только для общего названия и overview.
    Sections не отдаём модели на пересборку, чтобы она не сжала час лекции в 2 пункта.
    """
    outline = []

    for chunk in chunks:
        for section in chunk.sections:
            outline.append(
                f"- {format_seconds(section.start_time)}–{format_seconds(section.end_time)}: "
                f"{section.title}. {section.summary}"
            )

    prompt = f"""
На основе списка разделов лекции придумай короткое название и общий обзор.

Верни строго JSON:

{{
  "title": "Короткое название",
  "overview": "Краткое содержание материала в 4-8 предложениях"
}}

Правила:
- Только JSON.
- Не используй markdown.
- Не добавляй текст до или после JSON.
- Не пересобирай разделы.
- Не сокращай список разделов.
- Не выдумывай факты.

Разделы лекции:

{chr(10).join(outline[:80])}
""".strip()

    response = await client.generate(
        prompt=prompt,
        system_prompt=(
            "Ты редактор учебных материалов. "
            "Ты создаешь только название и общий обзор по готовому плану лекции. "
            "Ответ должен быть валидным JSON."
        ),
    )

    try:
        parsed = extract_json_object(response)
    except Exception:
        return {}

    title = parsed.get("title")
    overview = parsed.get("overview")

    result = {}

    if isinstance(title, str) and title.strip():
        result["title"] = title.strip()

    if isinstance(overview, str) and overview.strip():
        result["overview"] = overview.strip()

    return result


async def generate_chunk_json_with_retry(
    client: GigaChatClient,
    prompt: str,
    system_prompt: str,
    chunk_index: int,
    chunk_start: float,
    chunk_end: float,
    source_text: str,
) -> StructuredChunkSummary:
    """
    Генерирует structured JSON для одного чанка.

    Стратегия:
    1. обычная генерация;
    2. если JSON невалидный — просим модель исправить JSON;
    3. если снова невалидно — создаём fallback chunk, чтобы не валить весь часовой job.
    """
    last_response = ""
    last_error = ""

    response = await client.generate(
        prompt=prompt,
        system_prompt=system_prompt,
    )

    last_response = response

    try:
        return parse_structured_chunk_summary(
            text=response,
            chunk_index=chunk_index,
            chunk_start=chunk_start,
            chunk_end=chunk_end,
        )
    except Exception as e:
        last_error = str(e)

    repair_response = await client.generate(
        prompt=build_json_repair_prompt(
            bad_json=last_response,
            error_message=last_error,
            chunk_start=chunk_start,
            chunk_end=chunk_end,
        ),
        system_prompt=build_json_repair_system_prompt(),
    )

    try:
        return parse_structured_chunk_summary(
            text=repair_response,
            chunk_index=chunk_index,
            chunk_start=chunk_start,
            chunk_end=chunk_end,
        )
    except Exception:
        return build_fallback_chunk_summary(
            chunk_index=chunk_index,
            chunk_start=chunk_start,
            chunk_end=chunk_end,
            source_text=source_text,
        )


def build_json_repair_system_prompt() -> str:
    return (
        "Ты исправляешь невалидный JSON. "
        "Твоя задача — вернуть только валидный JSON-объект. "
        "Не добавляй пояснения, markdown или текст вокруг JSON."
    )


def build_json_repair_prompt(
    bad_json: str,
    error_message: str,
    chunk_start: float,
    chunk_end: float,
) -> str:
    return f"""
Исправь JSON ниже.

Ошибка парсинга:
{error_message}

Требования:
- Верни только валидный JSON.
- Все ключи должны быть в двойных кавычках.
- Все строки должны быть в двойных кавычках.
- Убери trailing commas.
- Не используй markdown.
- Не оборачивай JSON в ```json.
- Все временные значения должны быть числами.
- Все time/start_time/end_time должны быть в диапазоне {round(chunk_start, 2)}–{round(chunk_end, 2)}.

Ожидаемая структура:

{{
  "sections": [
    {{
      "title": "Название раздела",
      "start_time": {round(chunk_start, 2)},
      "end_time": {round(chunk_end, 2)},
      "summary": "Краткое содержание",
      "points": [
        {{
          "time": {round(chunk_start, 2)},
          "text": "Ключевой тезис"
        }}
      ]
    }}
  ],
  "terms": [],
  "questions": []
}}

Невалидный JSON:

{bad_json[:8000]}
""".strip()


def build_fallback_chunk_summary(
    chunk_index: int,
    chunk_start: float,
    chunk_end: float,
    source_text: str,
) -> StructuredChunkSummary:
    """
    Fallback нужен, чтобы один плохой JSON-ответ не ломал весь часовой конспект.

    Это не идеальный summary, но он сохраняет покрытие временного интервала.
    """
    cleaned_text = source_text.replace("\n", " ").strip()
    cleaned_text = " ".join(cleaned_text.split())

    if len(cleaned_text) > 700:
        cleaned_text = cleaned_text[:700].rsplit(" ", 1)[0] + "..."

    return StructuredChunkSummary(
        chunk_index=chunk_index,
        start_time=chunk_start,
        end_time=chunk_end,
        sections=[
            StructuredSection(
                id=None,
                title=f"Фрагмент {chunk_index}",
                start_time=chunk_start,
                end_time=chunk_end,
                summary=(
                    "Автоматическая структуризация этого фрагмента не удалась, "
                    "поэтому сохранён краткий фрагмент транскрипта: "
                    f"{cleaned_text}"
                ),
                points=[],
            )
        ],
        terms=[],
        questions=[],
    )
