# scripts/test_llm.py
"""
CLI-утилита для тестирования LLM модуля независимо от конвейера.

Использование:
    python -m scripts.test_llm --media-id <uuid>

Или с файлом транскрипции:
    python -m scripts.test_llm --transcript-file transcriptions.json
"""
import asyncio
import argparse
import json
import uuid
from pathlib import Path
from modules.shared.database import db_settings, AsyncSessionLocal
from modules.llm.service import LLMService
from modules.media.models import Transcription

# Импортируем модели для регистрации в SQLAlchemy
from modules.auth.models import User, RefreshToken
from modules.media.models import Media, ProcessingJob, Transcription
from modules.llm.models import Summary


async def test_with_media_id(media_id: uuid.UUID):
    """Тестирование с существующим media_id в БД"""
    print(f"Поиск транскрипции для media_id: {media_id}")

    async with AsyncSessionLocal() as db_session:
        service = LLMService(db_session)

        # Проверяем дедупликацию
        existing = await service.check_duplicate(media_id)
        if existing:
            print(f"Найден готовый конспект!")
            print(f"Статус: {existing.status}")
            print(f"Длина: {len(existing.content)} символов")
            return

        print("Запуск генерации конспекта...")
        summary = await service.generate_summary(media_id)

        print(f"Конспект создан!")
        print(f"Статус: {summary.status}")
        print(f"Длина: {len(summary.content)} символов")

        # Сохраняем в файл
        output_path = Path(f"summary_{media_id}.md")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(summary.content)

        print(f"Конспект сохранён в {output_path}")


async def test_with_transcript_file(file_path: str):
    """Тестирование с файлом транскрипции"""
    print(f"Загрузка транскрипции из {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        segments = json.load(f)

    print(f"Загружено {len(segments)} сегментов")

    # Создаём фейковый media_id для тестов
    test_media_id = uuid.uuid4()

    async with AsyncSessionLocal() as db_session:
        # Создаём тестовую транскрипцию в БД
        transcription = Transcription(
            id=uuid.uuid4(),
            media_id=test_media_id,
            segments=segments,
            full_text=" ".join([s.get("text", "") for s in segments]),
            model_name="test"
        )
        db_session.add(transcription)
        await db_session.commit()

        print("Запуск генерации конспекта...")
        service = LLMService(db_session)
        summary = await service.generate_summary(test_media_id)

        print(f"Конспект создан!")
        print(f"Длина: {len(summary.content)} символов")

        # Сохраняем в файл
        output_path = Path("summary_test.md")
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(summary.content)

        print(f"Конспект сохранён в {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Тестирование LLM модуля")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--media-id", type=str, help="UUID медиафайла в БД")
    group.add_argument("--transcript-file", type=str, help="Путь к JSON файлу с транскрипцией")

    args = parser.parse_args()

    if args.media_id:
        media_id = uuid.UUID(args.media_id)
        asyncio.run(test_with_media_id(media_id))
    elif args.transcript_file:
        asyncio.run(test_with_transcript_file(args.transcript_file))


if __name__ == "__main__":
    main()
