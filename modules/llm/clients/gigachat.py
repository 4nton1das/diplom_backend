# modules/llm/clients/gigachat.py
import asyncio
import logging
import time
from typing import List, Optional
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from modules.llm.config import llm_config
from modules.llm.clients.base import BaseLLMClient

logger = logging.getLogger(__name__)


class GigaChatClient(BaseLLMClient):
    """Клиент для GigaChat API (Sber)"""

    def __init__(self):
        self.authorization_key = llm_config.gigachat_authorization_key
        self.scope = llm_config.gigachat_scope
        self.model = llm_config.gigachat_model
        self.temperature = llm_config.temperature
        self.max_tokens = llm_config.max_tokens

        self.auth_url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
        self.api_url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

        # Кэш токена
        self.access_token = None
        self.token_expires_at = 0

        if not self.authorization_key:
            logger.error("GigaChat Authorization Key not configured!")
            raise ValueError("GigaChat authorization_key is required")

        logger.info(f"GigaChat client initialized")

    @retry(
        stop=stop_after_attempt(llm_config.max_retries),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError, ConnectionError))
    )
    async def _get_access_token(self) -> tuple[str, int]:
        """Получает access token (действует 30 минут)"""

        # Используем Authorization Key напрямую (уже Base64)
        headers = {
            'Authorization': f'Basic {self.authorization_key}',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json',
            'RqUID': '6f0b1291-c7f3-43c6-bb2e-9f3efb2dc98e'
        }

        data = {'scope': self.scope}

        logger.info(f"Requesting token from {self.auth_url}")

        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            try:
                response = await client.post(
                    self.auth_url,
                    headers=headers,
                    data=data
                )

                logger.info(f"Auth response status: {response.status_code}")

                if response.status_code != 200:
                    logger.error(f"Auth failed: {response.status_code} - {response.text}")

                response.raise_for_status()

                token_data = response.json()
                access_token = token_data['access_token']
                expires_at = token_data['expires_at']

                logger.info(f"GigaChat token obtained, expires at {expires_at}")
                return access_token, expires_at

            except httpx.HTTPStatusError as e:
                logger.error(f"GigaChat auth failed: {e.response.status_code} - {e.response.text}")
                raise

    async def _ensure_valid_token(self):
        """Проверяет и обновляет токен при необходимости"""
        current_time = int(time.time())

        if not self.access_token or current_time > self.token_expires_at - 60:
            self.access_token, self.token_expires_at = await self._get_access_token()

    @retry(
        stop=stop_after_attempt(llm_config.max_retries),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError))
    )
    async def generate(
            self,
            prompt: str,
            system_prompt: Optional[str] = None
    ) -> str:
        """Генерация ответа от GigaChat"""

        await self._ensure_valid_token()

        messages = []

        if system_prompt:
            messages.append({
                "role": "system",
                "content": system_prompt
            })

        messages.append({
            "role": "user",
            "content": prompt
        })

        logger.info(f"Calling GigaChat API with model: {self.model}")

        async with httpx.AsyncClient(timeout=120.0, verify=False) as client:
            response = await client.post(
                self.api_url,
                headers={
                    'Authorization': f'Bearer {self.access_token}',
                    'Content-Type': 'application/json'
                },
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": self.temperature,
                    "max_tokens": self.max_tokens
                }
            )

            logger.info(f"GigaChat API response status: {response.status_code}")

            response.raise_for_status()
            data = response.json()

            if 'choices' not in data or len(data['choices']) == 0:
                raise ValueError("GigaChat API: отсутствуют choices в ответе")

            if 'message' not in data['choices'][0]:
                raise ValueError("GigaChat API: отсутствует message в ответе")

            content = data['choices'][0]['message'].get('content', '')

            if not content:
                raise ValueError("GigaChat API: пустой content в ответе")

            return content

    async def generate_batch(
            self,
            prompts: List[str],
            system_prompt: Optional[str] = None
    ) -> List[str]:
        """Пакетная генерация (последовательно для GigaChat)"""

        results = []

        for i, prompt in enumerate(prompts):
            try:
                logger.info(f"GigaChat: Processing chunk {i + 1}/{len(prompts)}")
                result = await self.generate(prompt, system_prompt)
                results.append(result)
            except Exception as e:
                logger.error(f"GigaChat chunk {i} failed: {e}")
                results.append("")

        return results
