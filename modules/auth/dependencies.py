from typing import Optional, Annotated
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from modules.auth.service import get_user_service
from modules.auth.schemas import TokenData

# Схема для JWT токена в заголовке
security = HTTPBearer(auto_error=False)


async def get_current_user(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
        user_service=Depends(get_user_service),
) -> TokenData:
    """Получение текущего пользователя из JWT токена"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        token_data = await user_service.verify_token(credentials.credentials)
        return token_data
    except Exception as e:
        print(f"DEBUG AUTH ERROR: {type(e).__name__} - {str(e)}")
        raise HTTPException(
            status_code=401,
            detail=f"Auth Error: {str(e)}"
        )


async def get_current_active_user(
        current_user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Получение текущего активного пользователя"""
    # Здесь можно добавить проверку is_active, если нужно
    return current_user


def require_subscription(tier: str = "free"):
    """Декоратор для проверки подписки пользователя"""

    def subscription_checker(
            current_user: TokenData = Depends(get_current_active_user)
    ):
        if tier == "pro" and current_user.subscription_tier not in ["pro", "enterprise"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Subscription tier '{tier}' required"
            )
        if tier == "enterprise" and current_user.subscription_tier != "enterprise":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Subscription tier '{tier}' required"
            )
        return current_user

    return subscription_checker


# Аннотации для удобства
CurrentUser = Annotated[TokenData, Depends(get_current_active_user)]
ProUser = Annotated[TokenData, Depends(require_subscription("pro"))]
EnterpriseUser = Annotated[TokenData, Depends(require_subscription("enterprise"))]
