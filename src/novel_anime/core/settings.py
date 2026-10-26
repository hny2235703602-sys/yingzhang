from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="YINGZHANG_", extra="ignore")
    data_dir: Path = ROOT / "data"
    lease_seconds: int = 900
    poll_seconds: float = 1.0

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.data_dir / 'studio.db'}"


def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
