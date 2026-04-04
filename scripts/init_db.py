# scripts/init_db.py
"""
Утилита для инициализации базы данных.
Запускать перед первым запуском приложения или тестов.

Использование:
    python -m scripts.init_db
"""
import asyncio
from modules.shared.database import init_db, create_tables


async def main():
    print("Инициализация базы данных...")
    print("Создание схем...")
    await init_db()
    print("Создание таблиц...")
    await create_tables()
    print("База данных готова к работе!")


if __name__ == "__main__":
    asyncio.run(main())
