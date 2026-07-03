from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False
    )

    # Biwenger
    biwenger_email: str = ""
    biwenger_password: str = ""
    biwenger_league_id: int = 1809775
    biwenger_user_id: int = 11444346
    biwenger_account_id: int = 4019960
    biwenger_competition: str = "la-liga"
    biwenger_score_id: int = 2

    # SSL — disable on corporate networks with MITM proxy
    ssl_verify: bool = False

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""  # numeric ID or @username

    # Bot behaviour
    dry_run: bool = True  # Safety default — must explicitly set to false
    max_spend_per_jornada: int = 5_000_000
    min_squad_size: int = 15
    confirmation_timeout_seconds: int = 1800  # 30 min


settings = Settings()
