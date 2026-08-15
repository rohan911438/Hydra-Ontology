"""Environment configuration for the pipeline."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    hydra_bolt_uri: str
    hydra_auth_token: str
    hydra_database: str
    gemini_api_key: str | None
    gemini_model: str

    @classmethod
    def load(cls) -> "Config":
        return cls(
            hydra_bolt_uri=os.environ.get("HYDRA_BOLT_URI", "bolt://127.0.0.1:7687"),
            hydra_auth_token=os.environ.get("HYDRA_AUTH_TOKEN", "local-development-token-32-bytes"),
            hydra_database=os.environ.get("HYDRA_DATABASE", "default"),
            gemini_api_key=os.environ.get("GEMINI_API_KEY") or None,
            gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        )


CONFIG = Config.load()
