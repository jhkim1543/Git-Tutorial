"""Runtime settings. Secrets come only from server environment variables."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # figure-3dgen/
CONTRACTS = ROOT / "contracts"


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.environ.get(name, default))))
    except ValueError:
        return default


class Settings:
    def __init__(self) -> None:
        self.base_path = os.environ.get("FIG3D_BASE_PATH", "/3dgen").rstrip("/")
        self.data_dir = Path(os.environ.get("FIG3D_DATA_DIR", ROOT / "data")).resolve()
        # Astra = OpenAI Responses API. The existing server key is reused; it is never sent to the browser.
        self.astra_endpoint = os.environ.get("ASTRA_ENDPOINT", "https://api.openai.com/v1/responses")
        self.astra_model = os.environ.get("ASTRA_MODEL", "gpt-6-astra")
        self.astra_image_model = os.environ.get("ASTRA_IMAGE_MODEL", "")  # optional image tool model override
        self.astra_timeout = _env_int("ASTRA_TIMEOUT_SECONDS", 240, 30, 900)
        self.max_upload_mb = _env_int("FIG3D_MAX_UPLOAD_MB", 120, 5, 500)
        self.part_seconds = _env_int("FIG3D_BLENDER_PART_SECONDS", 300, 30, 3600)
        self.target_quads = _env_int("FIG3D_TARGET_QUADS", 6000, 500, 200000)

    @property
    def astra_key(self) -> str | None:
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        return key or None

    def blender_command(self) -> list[str] | None:
        """Blender executable, or the current Python when the `bpy` module is installed."""
        explicit = os.environ.get("FIG3D_BLENDER_PATH")
        if explicit and Path(explicit).is_file():
            return [explicit, "--background", "--factory-startup", "--python-exit-code", "1", "--python"]
        found = shutil.which("blender")
        if found:
            return [found, "--background", "--factory-startup", "--python-exit-code", "1", "--python"]
        if importlib.util.find_spec("bpy") is not None:
            return [sys.executable]
        return None


settings = Settings()


@lru_cache(maxsize=1)
def criteria() -> dict:
    return json.loads((CONTRACTS / "criteria.json").read_text(encoding="utf-8"))


def joint_library() -> dict:
    path = CONTRACTS / "joint_library.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"status": "EMPTY", "archetypes": []}
