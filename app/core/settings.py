from typing import Optional

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
    # Autenticação
    API_KEY: str
    # Segredo extra (2026-08-24) só pros endpoints de administração da Theme
    # Library do módulo de decks (POST/GET /decks/themes) — além do x-api-key
    # global (`validar_acesso`, que todo request já precisa), gerenciar a
    # marca de um cliente exige esta segunda chave. `None` (padrão) mantém
    # essas rotas fechadas por padrão — só abrem quando configurada de propósito.
    DECKS_THEME_ADMIN_KEY: Optional[str] = None
    # "local" habilita CORS permissivo (usado pela interface de teste local).
    # Qualquer outro valor (padrão) mantém CORS desligado, como em produção.
    ENV: str = "production"


settings = Settings()
