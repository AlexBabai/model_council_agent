import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    auth_token: str
    database_path: Path
    app_name: str = "Model Council Agent API"


@lru_cache
def get_settings() -> Settings:
    return Settings(
        auth_token=os.getenv("AGENT_AUTH_TOKEN", ""),
        database_path=Path(os.getenv("DATABASE_PATH", "db.sqlite")),
    )
