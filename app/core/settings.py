from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    SYSTEM_PROMPT: str
    OUTPUT_DIR: str
    SLIDE_DECK_INSTRUCTION: str
    DATABASE_URL: str
    # Horário do backup diário (fuso: America/Sao_Paulo)
    BACKUP_SCHEDULE_HOUR: int = 23
    BACKUP_SCHEDULE_MINUTE: int = 0


settings = Settings()
