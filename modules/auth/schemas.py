import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, field_validator
from fastapi_users import schemas


class UserRead(schemas.BaseUser[uuid.UUID]):
    """Схема для чтения пользователя"""
    id: uuid.UUID
    email: EmailStr
    is_active: bool = True
    is_superuser: bool = False
    is_verified: bool = False
    subscription_tier: str = "free"
    subscription_expires_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserCreate(schemas.BaseUserCreate):
    """Схема для создания пользователя"""
    email: EmailStr
    password: str

    @field_validator('password')
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError('Password must be at least 8 characters long')
        # Дополнительные проверки пароля
        return v


class UserUpdate(schemas.BaseUserUpdate):
    """Схема для обновления пользователя"""
    subscription_tier: Optional[str] = None


# Схемы для JWT токенов
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    user_id: Optional[uuid.UUID] = None
    email: Optional[str] = None
    subscription_tier: Optional[str] = None


# Схема для обновления подписки
class SubscriptionUpdate(BaseModel):
    tier: str
    expires_at: Optional[datetime] = None
