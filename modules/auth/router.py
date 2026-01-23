from typing import Annotated
from datetime import datetime, UTC

from fastapi import APIRouter, Depends, HTTPException, status, Body
from fastapi.security import OAuth2PasswordRequestForm

from modules.auth.service import UserService, get_user_service
from modules.auth.schemas import UserCreate, UserRead, Token, SubscriptionUpdate, LogoutRequest
from modules.auth.dependencies import CurrentUser, require_subscription
from modules.auth.models import User, RefreshToken
from modules.shared.event_bus import event_bus

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(
        user_create: UserCreate,
        user_service: UserService = Depends(get_user_service),
):
    """Регистрация нового пользователя"""
    try:
        user = await user_service.create_user(user_create)
        return user
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/login", response_model=Token)
async def login(
        form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
        user_service: UserService = Depends(get_user_service),
):
    """Вход пользователя и получение токенов"""
    user = await user_service.authenticate_user(form_data.username, form_data.password)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Создаем токены
    access_token = user_service.create_access_token(
        data={"sub": str(user.id), "email": user.email, "subscription_tier": user.subscription_tier}
    )
    refresh_token = user_service.create_refresh_token(
        data={"sub": str(user.id)}
    )

    # Сохраняем refresh токен
    await user_service.save_refresh_token(user.id, refresh_token)

    return Token(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer"
    )


@router.post("/refresh", response_model=Token)
async def refresh_token(
        refresh_token: str,
        user_service: UserService = Depends(get_user_service),
):
    """Обновление access токена по refresh токену"""
    user = await user_service.verify_refresh_token(refresh_token)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Создаем новый access токен
    new_access_token = user_service.create_access_token(
        data={"sub": str(user.id), "email": user.email, "subscription_tier": user.subscription_tier}
    )

    return Token(
        access_token=new_access_token,
        refresh_token=refresh_token,  # Тот же refresh токен
        token_type="bearer"
    )


@router.post("/logout")
async def logout(
        current_user: CurrentUser,
        logout_data: LogoutRequest,
        user_service: UserService = Depends(get_user_service),
):
    """Выход пользователя (отзыв refresh токена)"""
    from sqlalchemy import update

    # Помечаем refresh токен как отозванный
    stmt = update(RefreshToken).where(
        RefreshToken.token == logout_data.refresh_token,
        RefreshToken.user_id == current_user.user_id
    ).values(revoked_at=datetime.now(UTC))

    await user_service.db.execute(stmt)
    await user_service.db.commit()

    return {"message": "Successfully logged out"}


@router.get("/me", response_model=UserRead)
async def read_users_me(
        current_user: CurrentUser,
        user_service: UserService = Depends(get_user_service)
):
    """Получение полной информации о текущем пользователе"""
    # Достаем пользователя из БД по ID, который пришел в токене
    from sqlalchemy import select
    result = await user_service.db.execute(
        select(User).where(User.id == current_user.user_id)
    )
    db_user = result.scalar_one_or_none()

    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    return db_user


@router.put("/me/subscription", dependencies=[Depends(require_subscription("pro"))])
async def update_subscription(
        subscription_update: SubscriptionUpdate,
        current_user: CurrentUser,
        user_service: UserService = Depends(get_user_service),
):
    """Обновление подписки пользователя (только для pro и выше)"""
    from sqlalchemy import update
    from modules.auth.events import UserSubscriptionUpdated

    # Обновляем подписку пользователя
    stmt = update(User).where(
        User.id == current_user.user_id
    ).values(
        subscription_tier=subscription_update.tier,
        subscription_expires_at=subscription_update.expires_at
    )

    await user_service.db.execute(stmt)
    await user_service.db.commit()

    # Отправляем событие
    await event_bus.publish(UserSubscriptionUpdated(
        user_id=current_user.user_id,
        old_tier=current_user.subscription_tier,
        new_tier=subscription_update.tier,
        updated_at=datetime.now(UTC),
        expires_at=subscription_update.expires_at
    ))

    return {"message": "Subscription updated successfully"}


@router.get("/health")
async def health_check():
    """Проверка работоспособности модуля auth"""
    return {"status": "ok", "module": "auth"}
