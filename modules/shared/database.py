# shared/database.py
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import declarative_base
from pydantic_settings import BaseSettings, SettingsConfigDict


# 1. Создаем класс для чтения настроек базы
class DbSettings(BaseSettings):
    database_url: str  # Pydantic сам найдет DATABASE_URL в .env

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"  # Игнорируем секреты JWT, которые лежат в том же .env
    )


db_settings = DbSettings()

# 2. Настраиваем SQLAlchemy, используя данные из DbSettings
engine = create_async_engine(db_settings.database_url, echo=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


# 3. Функции для работы с сессиями и базой
async def get_db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Инициализация подключения к БД"""
    async with engine.begin() as conn:
        # Создаем схемы
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS auth"))
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS media"))
        await conn.run_sync(Base.metadata.create_all)


async def create_tables():
    """Создание таблиц"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
