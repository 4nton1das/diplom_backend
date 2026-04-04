# modules/llm/clients/__init__.py
from modules.llm.clients.base import BaseLLMClient
from modules.llm.clients.gigachat import GigaChatClient

__all__ = ["BaseLLMClient", "GigaChatClient"]
