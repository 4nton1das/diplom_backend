# modules/media/tasks.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from modules.media.config import media_config

# Синхронный engine (заменяем asyncpg на обычный postgresql)
SYNC_DATABASE_URL = media_config.database_url.replace('+asyncpg', '')  # предполагаем, что в media_config есть database_url
# Если нет, добавь в media_config поле database_url, читаемое из .env

engine = create_engine(SYNC_DATABASE_URL)
Session = sessionmaker(bind=engine)
