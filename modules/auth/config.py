from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthConfig(BaseSettings):
    # JWT настройки (Pydantic сам подставит значения из .env, если они там есть)
    secret_key: str = "secret-key-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1
    refresh_token_expire_days: int = 7

    # Настройки для cookies
    cookie_name: str = "auth_token"
    cookie_secure: bool = False
    cookie_httponly: bool = True
    cookie_samesite: str = "lax"

    # Настройки для верификации email (пока пропускаем)
    verification_token_secret: str = "verification-secret"

    # Настройки для сброса пароля
    reset_password_token_secret: str = "reset-secret"

    # CORS настройки
    cors_origins: list = ["http://localhost:3000", "http://localhost:5173"]

    # Настройки подписки
    subscription_default_tier: str = "free"  # free, pro, enterprise

    # Настройка чтения .env
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"  # Игнорировать DATABASE_URL
    )


auth_config = AuthConfig()
