"""
Configuration loaded from environment variables.

Uses pydantic-settings when installed, and falls back to a plain
``os.environ``-backed reader so the project runs out of the box without
extra dependencies. All external-service credentials and runtime knobs live
here so tests can monkeypatch a single object.

    from config import settings
"""

from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


def _load_dotenv() -> None:
    """Populate os.environ from a sibling .env file if present.

    Tiny in-house parser so we don't add python-dotenv as a hard
    dependency. Lines of the form ``KEY=value`` (with optional surrounding
    quotes, optional ``export`` prefix, and ``#`` comments) are loaded.
    Existing environment variables always win — .env never overrides
    something the shell already set.
    """
    here = Path(__file__).resolve().parent
    # Look in the tests/ folder first, then the project root, then CWD.
    candidates = [
        here / ".env",
        here.parent / ".env",
        Path.cwd() / ".env",
    ]
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.exists():
            continue
        seen.add(resolved)
        try:
            for raw in resolved.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Strip an inline trailing comment unless it's inside quotes.
                if value and value[0] not in ("'", '"'):
                    hash_idx = value.find(" #")
                    if hash_idx != -1:
                        value = value[:hash_idx].rstrip()
                # Strip surrounding quotes
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            continue


_load_dotenv()


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    val = os.environ.get(name)
    return val if val not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except (TypeError, ValueError):
        return default


class Settings(BaseModel):
    # Azure Document Intelligence (PDF semantic layout / OCR)
    azure_di_endpoint: Optional[str] = None
    azure_di_key: Optional[str] = None

    # Azure OpenAI (section mapping + rewriting)
    azure_openai_endpoint: Optional[str] = None
    azure_openai_key: Optional[str] = None
    azure_openai_deployment: Optional[str] = None
    azure_openai_api_version: str = "2024-08-01-preview"

    # LibreOffice
    soffice_bin: str = ""

    # Domain profiles directory
    domain_profiles_dir: str = ""

    # Upload limit (MB) used to validate /process inputs
    max_upload_mb: int = 100

    # Threshold for OCR fallback — average chars per page below which a PDF
    # is treated as scanned.
    ocr_char_threshold: int = 50

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            azure_di_endpoint=_env("AZURE_DI_ENDPOINT"),
            azure_di_key=_env("AZURE_DI_KEY"),
            azure_openai_endpoint=_env("AZURE_OPENAI_ENDPOINT"),
            azure_openai_key=_env("AZURE_OPENAI_KEY"),
            azure_openai_deployment=_env("AZURE_OPENAI_DEPLOYMENT"),
            azure_openai_api_version=_env(
                "AZURE_OPENAI_API_VERSION", "2024-08-01-preview"
            ) or "2024-08-01-preview",
            soffice_bin=_env("SOFFICE_BIN", "") or "",
            domain_profiles_dir=_env("DOMAIN_PROFILES_DIR", "") or "",
            max_upload_mb=_env_int("MAX_UPLOAD_MB", 100),
            ocr_char_threshold=_env_int("OCR_CHAR_THRESHOLD", 50),
        )

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def resolved_soffice(self) -> Optional[str]:
        if self.soffice_bin and os.path.exists(self.soffice_bin):
            return self.soffice_bin
        for candidate in ("soffice", "libreoffice"):
            found = shutil.which(candidate)
            if found:
                return found
        mac_default = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
        if os.path.exists(mac_default):
            return mac_default
        return None

    def resolved_domain_profiles_dir(self) -> str:
        if self.domain_profiles_dir:
            return self.domain_profiles_dir
        return os.path.join(os.path.dirname(__file__), "domain_profiles")

    def azure_di_configured(self) -> bool:
        return bool(self.azure_di_endpoint and self.azure_di_key)

    def azure_openai_configured(self) -> bool:
        return bool(
            self.azure_openai_endpoint
            and self.azure_openai_key
            and self.azure_openai_deployment
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


settings = get_settings()
