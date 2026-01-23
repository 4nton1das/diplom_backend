from datetime import datetime, timedelta, UTC
from typing import Optional
from fastapi import Depends
import uuid

from jose import JWTError, jwt
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher
from sqlalchemy.ext.asyncio import AsyncSession

from modules.auth.config import auth_config
from modules.auth.models import User, RefreshToken
from modules.auth.schemas import TokenData, UserCreate
from modules.auth.events import UserRegistered, UserLoggedIn
from modules.shared.event_bus import event_bus
from modules.shared.database import get_db_session

password_hasher = PasswordHash(hashers=[BcryptHasher()])


class UserService:
    """Сервис для работы с пользователями"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    # Хеширование пароля
    @staticmethod
    def hash_password(password: str) -> str:
        return password_hasher.hash(password)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        return password_hasher.verify(plain_password, hashed_password)

    # JWT токены
    @staticmethod
    def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
        to_encode = data.copy()
        now = datetime.now(UTC)
        if expires_delta:
            expire = now + expires_delta
        else:
            expire = now + timedelta(minutes=auth_config.access_token_expire_minutes)

        to_encode.update({
            "exp": int(expire.timestamp()),
            "sub": str(data.get("sub")),
            "type": "access"
        })
        encoded_jwt = jwt.encode(
            to_encode, auth_config.secret_key, algorithm=auth_config.algorithm
        )
        return encoded_jwt

    @staticmethod
    def create_refresh_token(data: dict) -> str:
        to_encode = data.copy()
        now = datetime.now(UTC)
        expire = now + timedelta(days=auth_config.refresh_token_expire_days)
        to_encode.update({
            "exp": int(expire.timestamp()),
            "sub": str(data.get("sub")),
            "type": "refresh"
        })
        encoded_jwt = jwt.encode(
            to_encode, auth_config.secret_key, algorithm=auth_config.algorithm
        )
        return encoded_jwt

    # Работа с пользователями
    async def create_user(self, user_create: UserCreate) -> User:
        """Создание нового пользователя"""
        # Проверяем, нет ли уже пользователя с таким email
        from sqlalchemy import select
        result = await self.db.execute(
            select(User).where(User.email == user_create.email)
        )
        existing_user = result.scalar_one_or_none()

        if existing_user:
            raise ValueError("User with this email already exists")

        # Создаем пользователя
        db_user = User(
            email=user_create.email,
            hashed_password=self.hash_password(user_create.password),
            full_name=user_create.full_name,
            subscription_tier=auth_config.subscription_default_tier,
        )

        self.db.add(db_user)
        await self.db.commit()
        await self.db.refresh(db_user)

        # Отправляем событие о регистрации
        await event_bus.publish(UserRegistered(
            user_id=db_user.id,
            email=db_user.email,
            registered_at=datetime.now(),
            subscription_tier=db_user.subscription_tier,
            full_name=db_user.full_name
        ))

        return db_user

    async def authenticate_user(self, email: str, password: str) -> Optional[User]:
        """Аутентификация пользователя"""
        from sqlalchemy import select

        result = await self.db.execute(
            select(User).where(User.email == email)
        )
        user = result.scalar_one_or_none()

        if not user:
            return None
        if not self.verify_password(password, user.hashed_password):
            return None
        if not user.is_active:
            return None

        # Отправляем событие о входе
        await event_bus.publish(UserLoggedIn(
            user_id=user.id,
            login_at=datetime.now()
        ))

        return user

    @staticmethod
    async def verify_token(token: str) -> TokenData:
        """Верификация JWT токена"""
        try:
            payload = jwt.decode(
                token, auth_config.secret_key, algorithms=[auth_config.algorithm]
            )
            user_id: str = payload.get("sub")
            email: str = payload.get("email")
            subscription_tier: str = payload.get("subscription_tier", "free")

            if user_id is None or email is None:
                raise JWTError("Invalid token payload")

            return TokenData(
                user_id=uuid.UUID(user_id),
                email=email,
                subscription_tier=subscription_tier
            )
        except JWTError as e:
            raise ValueError(f"Could not validate credentials: {str(e)}")

    async def save_refresh_token(self, user_id: uuid.UUID, token: str) -> None:
        """Сохранение refresh токена в БД"""
        expires_at = datetime.now(UTC) + timedelta(days=auth_config.refresh_token_expire_days)

        db_token = RefreshToken(
            user_id=user_id,
            token=token,
            expires_at=expires_at
        )

        self.db.add(db_token)
        await self.db.commit()

    async def verify_refresh_token(self, token: str) -> Optional[User]:
        """Проверка refresh токена"""
        from sqlalchemy import select

        try:
            # Проверяем JWT
            payload = jwt.decode(
                token, auth_config.secret_key, algorithms=[auth_config.algorithm]
            )
            user_id: str = payload.get("sub")
            token_type: str = payload.get("type")

            if token_type != "refresh" or user_id is None:
                return None

            # Проверяем, что токен есть в БД и не отозван
            result = await self.db.execute(
                select(RefreshToken).where(
                    RefreshToken.token == token,
                    RefreshToken.expires_at > datetime.now(UTC),
                    RefreshToken.revoked_at is None
                )
            )
            db_token = result.scalar_one_or_none()

            if not db_token:
                return None

            # Получаем пользователя
            result = await self.db.execute(
                select(User).where(User.id == uuid.UUID(user_id))
            )
            return result.scalar_one_or_none()

        except JWTError:
            return None


# Dependency для инжекции сервиса
async def get_user_service(db_session: AsyncSession = Depends(get_db_session)):
    return UserService(db_session)
