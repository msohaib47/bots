from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://swingalert:swingalert@localhost:5432/swingalert"

    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_url: str = "https://data.alpaca.markets"

    ntfy_base_url: str = "https://ntfy.sh"
    ntfy_global_topic: str = "swing-alerts-global"
    ntfy_user_topic_prefix: str = "swing-alerts-"

    default_symbols: str = "SPY,QQQ,AAPL,MSFT,NVDA"

    alert_cooldown_minutes: int = 240

    score_strong_buy: int = 8
    score_buy: int = 3
    score_sell: int = -3
    score_strong_sell: int = -8

    bar_sync_interval_minutes: int = 5
    enable_scheduler: bool = True  # tests/one-off containers (e.g. `alembic` only) set this false

    reports_dir: str = "reports"
    backtest_initial_capital: float = 10000.0

    log_level: str = "INFO"

    @property
    def default_symbol_list(self) -> list[str]:
        return [s.strip().upper() for s in self.default_symbols.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
