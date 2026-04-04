# modules/llm/config.py
from pydantic_settings import BaseSettings
from typing import Literal


class LLMConfig(BaseSettings):
    # Выбор провайдера
    provider: Literal["gigachat", "mock"] = "gigachat"

    # GigaChat API настройки
    gigachat_authorization_key: str = ""  # Готовый Base64 из ЛК
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat:latest"

    # Параметры чанкинга
    chunk_size_tokens: int = 5000
    chunk_overlap_tokens: int = 500
    max_chunks_per_job: int = 20

    # Параметры генерации
    temperature: float = 0.3
    max_tokens: int = 4000

    # Retry настройки
    max_retries: int = 3
    retry_delay_seconds: int = 5

    class Config:
        env_file = ".env"
        extra = "ignore"


llm_config = LLMConfig()
