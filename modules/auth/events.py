from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import uuid


@dataclass
class UserRegistered:
    """Событие регистрации пользователя"""
    user_id: uuid.UUID
    email: str
    registered_at: datetime
    subscription_tier: str


@dataclass
class UserLoggedIn:
    """Событие входа пользователя"""
    user_id: uuid.UUID
    login_at: datetime
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


@dataclass
class UserSubscriptionUpdated:
    """Событие обновления подписки"""
    user_id: uuid.UUID
    old_tier: str
    new_tier: str
    updated_at: datetime
    expires_at: Optional[datetime] = None
