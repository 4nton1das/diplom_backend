# shared/event_bus.py
from typing import Dict, List, Callable, Any
import asyncio


class EventBus:
    """Простая шина событий в памяти"""

    def __init__(self):
        self._handlers: Dict[str, List[Callable]] = {}

    def subscribe(self, event_type: str, handler: Callable):
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    async def publish(self, event: Any):
        event_type = event.__class__.__name__
        if event_type in self._handlers:
            for handler in self._handlers[event_type]:
                # Запускаем обработчики асинхронно
                await asyncio.create_task(handler(event))


# Глобальная шина событий
event_bus = EventBus()
