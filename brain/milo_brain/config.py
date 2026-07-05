"""Brain configuration (``~/.milo-brain/config.yaml``) + GPU tier detection.

Tiers pick model sizes (spec §6):
    small (e.g. RTX 4050 6GB):  llama3.2:3b + whisper-small
    large (e.g. RTX 5090 32GB): 8B-class LLM + whisper-medium
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml

DEFAULT_DIR = Path.home() / ".milo-brain"

TIER_DEFAULTS = {
    "small": {"llm_model": "llama3.2:3b", "whisper_model": "small"},
    "large": {"llm_model": "llama3.1:8b", "whisper_model": "medium"},
}


def detect_gpu() -> tuple[str, int]:
    """(GPU name, VRAM MiB) via nvidia-smi; ("cpu", 0) when unavailable."""
    if shutil.which("nvidia-smi") is None:
        return "cpu", 0
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip().splitlines()
        name, mem = out[0].rsplit(",", 1)
        return name.strip(), int(mem.strip())
    except Exception:
        return "cpu", 0


def tier_for_vram(vram_mib: int) -> str:
    return "large" if vram_mib >= 16_000 else "small"


@dataclass
class BrainConfig:
    brain_id: str = ""
    name: str = field(default_factory=socket.gethostname)
    port: int = 8765
    tier: str = ""                # auto-detected when empty
    gpu: str = ""
    llm_model: str = ""
    whisper_model: str = ""
    ollama_url: str = "http://127.0.0.1:11434"
    piper_voice: str = "en_US-lessac-medium"
    face_match_threshold: float = 0.45
    vision_fps: float = 3.0
    busy_gpu_percent: int = 85    # above this, advertise busy=1
    data_dir: str = str(DEFAULT_DIR)

    @property
    def paired_path(self) -> Path:
        return Path(self.data_dir) / "paired.json"

    @classmethod
    def load(cls, path: Path | None = None) -> "BrainConfig":
        path = path or DEFAULT_DIR / "config.yaml"
        cfg = cls(**(yaml.safe_load(path.read_text(encoding="utf-8")) or {})) if path.exists() else cls()
        changed = False
        if not cfg.brain_id:
            cfg.brain_id = f"brain-{uuid.uuid4().hex[:12]}"
            changed = True
        if not cfg.tier:
            cfg.gpu, vram = detect_gpu()
            cfg.tier = tier_for_vram(vram)
            changed = True
        defaults = TIER_DEFAULTS[cfg.tier if cfg.tier in TIER_DEFAULTS else "small"]
        if not cfg.llm_model:
            cfg.llm_model = defaults["llm_model"]
            changed = True
        if not cfg.whisper_model:
            cfg.whisper_model = defaults["whisper_model"]
            changed = True
        if changed:
            cfg.save(path)
        return cfg

    def save(self, path: Path | None = None) -> None:
        path = path or DEFAULT_DIR / "config.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(asdict(self), sort_keys=False), encoding="utf-8")
