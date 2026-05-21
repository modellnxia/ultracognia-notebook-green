from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    SYSTEM_PROMPT: str
    OUTPUT_DIR: str
    SLIDE_DECK_INSTRUCTION: str
    DATABASE_URL: str


settings = Settings()
