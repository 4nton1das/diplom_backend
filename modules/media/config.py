import os
from pydantic_settings import BaseSettings


class MediaConfig(BaseSettings):
    upload_dir: str = "uploads"
    max_file_size_mb: int = 500
    allowed_extensions: set = {"mp4", "avi", "mov", "mkv", "mp3", "wav", "m4a"}
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/diplom"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/0"

    def get_upload_path(self) -> str:
        # Создаем папку, если нет
        if not os.path.exists(self.upload_dir):
            os.makedirs(self.upload_dir)
        return self.upload_dir


media_config = MediaConfig()
