# modules/llm/service.py
import logging
import uuid
import json
from datetime import datetime, UTC
from typing import List, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from modules.llm.config import llm_config
from modules.llm.models import Summary, SummaryStatus
from modules.media.models import Transcription, Media
from modules.llm.clients.gigachat import GigaChatClient

logger = logging.getLogger(__name__)


class LLMService:
    """Сервис для генерации конспектов"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

        # Выбираем клиент
        if llm_config.provider == "gigachat":
            logger.info("Using GigaChat API Client")
            self.client = GigaChatClient()
        else:
            logger.warning("Unknown provider, using GigaChat")
            self.client = GigaChatClient()

    async def check_duplicate(self, media_id: uuid.UUID) -> Optional[Summary]:
        """Проверяем, есть ли уже готовый конспект"""
        result = await self.db.execute(
            select(Summary).where(
                Summary.media_id == media_id,
                Summary.status == SummaryStatus.completed
            )
        )
        return result.scalar_one_or_none()

    def chunk_transcription(
            self,
            segments: List[dict],
            chunk_size: int = 5000
    ) -> List[Tuple[List[dict], float, float]]:
        """
        Разбиваем транскрипцию на чанки по токенам.
        Возвращает: [(сегменты_чанка, start_time, end_time), ...]
        """
        chunks = []
        current_chunk = []
        current_tokens = 0
        chunk_start_time = 0.0

        for seg in segments:
            # Приблизительный подсчёт токенов (1 токен ≈ 4 символа для русского)
            seg_tokens = len(seg.get("text", "")) // 4

            if current_tokens + seg_tokens > chunk_size and current_chunk:
                # Завершаем текущий чанк
                chunk_end_time = current_chunk[-1].get("end", 0)
                chunks.append((current_chunk, chunk_start_time, chunk_end_time))

                # Начинаем новый с перекрытием
                overlap_segments = current_chunk[-3:]  # Последние 3 сегмента
                current_chunk = overlap_segments.copy()
                current_tokens = sum(len(s.get("text", "")) // 4 for s in current_chunk)
                chunk_start_time = overlap_segments[0].get("start", 0) if overlap_segments else 0

            current_chunk.append(seg)
            current_tokens += seg_tokens

        # Добавляем последний чанк
        if current_chunk:
            chunk_end_time = current_chunk[-1].get("end", 0)
            chunks.append((current_chunk, chunk_start_time, chunk_end_time))

        return chunks

    def create_map_prompt(self, segments: List[dict], chunk_id: int) -> str:
        """Создаёт промпт для MAP этапа (обработка чанка)"""

        text = "\n".join([
            f"[{self.client.format_timestamp(seg['start'])}] {seg['text']}"
            for seg in segments
        ])

        return f"""
Ты — помощник для создания структурированных конспектов из транскрипций.

ЗАДАЧА: Проанализируй следующую часть транскрипции и выдели ключевую информацию.

ТРАНСКРИПЦИЯ (часть {chunk_id}):
{text}

ВЕРНИ ОТВЕТ В ФОРМАТЕ JSON:
{{
    "key_points": [
        {{
            "timestamp": "00:05:23",
            "text": "Краткое описание ключевой мысли",
            "type": "concept|definition|example|important"
        }}
    ],
    "summary": "Краткое содержание этой части (2-3 предложения)",
    "topics": ["тема1", "тема2"]
}}

Требования:
1. Выделяй только действительно важные моменты
2. Указывай точные временные метки из транскрипции
3. Типы: concept (понятие), definition (определение), example (пример), important (важно)
4. summary должен быть кратким но содержательным
5. topics — основные темы этого фрагмента

Ответ ТОЛЬКО JSON, без дополнительного текста.
""".strip()

    def create_reduce_prompt(self, map_results: List[dict]) -> str:
        """Создаёт промпт для REDUCE этапа (объединение результатов)"""

        chunks_info = []
        for i, result in enumerate(map_results):
            if result:
                chunks_info.append(f"""
ЧАСТЬ {i + 1}:
Ключевые моменты:
{json.dumps(result.get('key_points', []), ensure_ascii=False, indent=2)}
Темы: {', '.join(result.get('topics', []))}
Кратко: {result.get('summary', '')}
---
""")

        return f"""
Ты — помощник для создания структурированных конспектов.

ЗАДАЧА: Объедини результаты анализа частей транскрипции в единый структурированный конспект.

РЕЗУЛЬТАТЫ АНАЛИЗА ЧАСТЕЙ:
{''.join(chunks_info)}

ВЕРНИ ОТВЕТ В ФОРМАТЕ MARKDOWN:

# [Придумай заголовок по содержанию]

## 📊 Общая информация
- **Длительность:** [укажи]
- **Ключевые темы:** [перечисли]

## 📑 Содержание
[Сделай оглавление с якорями и временными метками в формате [00:05:23](#якорь)]

## 🔑 Ключевые моменты

[Для каждой ключевой точки из map_results:]
### [Название раздела]
[00:05:23] [Текст ключевой точки]
> [Развёрнутое объяснение если есть]

## 📝 Краткое содержание
[Общий summary всего материала, 3-5 предложений]

Требования:
1. Используй Markdown форматирование
2. Временные метки должны быть кликабельными якорями
3. Группируй ключевые точки по темам/разделам
4. Сохраняй хронологический порядок
5. Язык — русский
""".strip()

    async def generate_summary(self, media_id: uuid.UUID) -> Summary:
        """Основной метод генерации конспекта (Map-Reduce)"""

        # 1. Получаем транскрипцию
        trans_result = await self.db.execute(
            select(Transcription).where(Transcription.media_id == media_id)
        )
        transcription = trans_result.scalar_one_or_none()

        if not transcription:
            raise ValueError(f"Transcription not found for media {media_id}")

        segments = transcription.segments  # List[dict]

        # 2. Создаём запись Summary
        summary = Summary(
            id=uuid.uuid4(),
            media_id=media_id,
            content="",
            status=SummaryStatus.processing,
            model_name=llm_config.gigachat_model,
            provider=llm_config.provider
        )
        self.db.add(summary)
        await self.db.commit()

        try:
            # 3. Chunking (разбиение на части)
            chunks = self.chunk_transcription(
                segments,
                chunk_size=llm_config.chunk_size_tokens
            )

            logger.info(f"Created {len(chunks)} chunks for media {media_id}")

            if len(chunks) > llm_config.max_chunks_per_job:
                logger.warning(f"Too many chunks ({len(chunks)}), limiting to {llm_config.max_chunks_per_job}")
                chunks = chunks[:llm_config.max_chunks_per_job]

            # 4. MAP этап: обрабатываем каждый чанк
            map_prompts = [
                self.create_map_prompt(chunk_segments, i)
                for i, (chunk_segments, _, _) in enumerate(chunks)
            ]

            logger.info("Starting MAP phase...")
            map_results_raw = await self.client.generate_batch(map_prompts)

            # Парсим JSON результаты
            map_results = []
            for i, raw in enumerate(map_results_raw):
                try:
                    # Извлекаем JSON из ответа (может быть обёрнут в markdown)
                    json_start = raw.find("{")
                    json_end = raw.rfind("}") + 1
                    if json_start >= 0 and json_end > json_start:
                        parsed = json.loads(raw[json_start:json_end])
                        map_results.append(parsed)
                    else:
                        logger.warning(f"Chunk {i}: No JSON found in response")
                        map_results.append({})
                except json.JSONDecodeError as e:
                    logger.error(f"Chunk {i}: JSON parse error: {e}")
                    map_results.append({})

            logger.info(f"MAP phase completed: {len(map_results)} results")

            # 5. REDUCE этап: объединяем результаты
            logger.info("Starting REDUCE phase...")
            reduce_prompt = self.create_reduce_prompt(map_results)
            final_content = await self.client.generate(reduce_prompt)

            # 6. Сохраняем результат
            summary.content = final_content
            summary.status = SummaryStatus.completed
            summary.completed_at = datetime.now(UTC)
            summary.tokens_used = len(final_content) // 4  # Приблизительно

            # Сохраняем структурированные данные для фронтенда
            summary.content_json = {
                "map_results": map_results,
                "chunks_count": len(chunks)
            }

            await self.db.commit()

            # 7. Обновляем статус Media
            media_result = await self.db.execute(
                select(Media).where(Media.id == media_id)
            )
            media = media_result.scalar_one_or_none()
            if media:
                media.status = "summarized"
                media.processing_stage = None
                media.processed_at = datetime.now(UTC)
                await self.db.commit()

            logger.info(f"Summary completed for media {media_id}")
            return summary

        except Exception as e:
            # Обработка ошибки
            summary.status = SummaryStatus.failed
            summary.error_message = str(e)
            await self.db.commit()

            # Обновляем статус Media
            media_result = await self.db.execute(
                select(Media).where(Media.id == media_id)
            )
            media = media_result.scalar_one_or_none()
            if media:
                media.status = "failed"
                media.error_message = str(e)
                await self.db.commit()

            logger.error(f"Summary failed for media {media_id}: {e}")
            raise
