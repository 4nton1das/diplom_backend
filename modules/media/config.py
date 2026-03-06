import os
from pydantic_settings import BaseSettings


class MediaConfig(BaseSettings):
    upload_dir: str = "uploads"
    max_file_size_mb: int = 500
    allowed_extensions: set = {"mp4", "avi", "mov", "mkv", "mp3", "wav", "m4a"}

    def get_upload_path(self) -> str:
        # Создаем папку, если нет
        if not os.path.exists(self.upload_dir):
            os.makedirs(self.upload_dir)
        return self.upload_dir


media_config = MediaConfig()
