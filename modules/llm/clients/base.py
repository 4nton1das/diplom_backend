# modules/llm/clients/base.py
from abc import ABC, abstractmethod
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


class BaseLLMClient(ABC):
    """Базовый класс для LLM клиентов"""

    @abstractmethod
    async def generate(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Генерация ответа от LLM"""
        pass

    @abstractmethod
    async def generate_batch(
            self,
            prompts: List[str],
            system_prompt: Optional[str] = None
    ) -> List[str]:
        """Пакетная генерация (для Map этапа)"""
        pass

    def format_timestamp(self, seconds: float) -> str:
        """Конвертация секунд в формат ЧЧ:ММ:СС"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"
