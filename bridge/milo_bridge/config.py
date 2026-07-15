"""Bridge configuration: identity, file locations, tunables.

Loaded from ``~/.milo/config.json`` (created with defaults on first run).
Secrets (pairing tokens) live in a separate file so config can be shared freely.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

DEFAULT_DIR = Path.home() / ".milo"


@dataclass
class BridgeConfig:
    robot_id: str = ""
    robot_name: str = "milo"
    data_dir: str = str(DEFAULT_DIR)

    # Servo tuning (per-channel calibrated pulse range, microseconds)
    servo_pulse_ranges: list[tuple[int, int]] = field(
        default_factory=lambda: [(500, 2500)] * 8
    )
    servo_stagger_ms: int = 20

    # Streaming
    video_fps: int = 15
    video_size: tuple[int, int] = (640, 480)
    audio_frame_ms: int = 20

    # Sleep mode
    loud_rms_threshold: float = 2000.0  # int16 RMS that perks Milo up while asleep
    reconnect_seconds: float = 10.0

    # Web dashboard
    web_enabled: bool = True
    web_port: int = 80
    web_username: str = "dama"
    web_password_hash: str = ""   # scrypt "<salt_hex>$<hash_hex>"; seeded on first load()

    @property
    def paired_path(self) -> Path:
        return Path(self.data_dir) / "paired.json"

    @property
    def graph_db_path(self) -> Path:
        return Path(self.data_dir) / "graph.db"

    @classmethod
    def load(cls, path: Path | None = None) -> "BridgeConfig":
        path = path or DEFAULT_DIR / "config.json"
        if path.exists():
            cfg = cls(**json.loads(path.read_text(encoding="utf-8")))
        else:
            cfg = cls()
        if not cfg.robot_id:
            cfg.robot_id = f"milo-{uuid.uuid4().hex[:12]}"
            cfg.save(path)
        if not cfg.web_password_hash:
            from .webapp.auth import hash_password
            cfg.web_password_hash = hash_password("MILO@gate")
            cfg.save(path)
        return cfg

    def save(self, path: Path | None = None) -> None:
        path = path or DEFAULT_DIR / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data["video_size"] = list(self.video_size)
        data["servo_pulse_ranges"] = [list(r) for r in self.servo_pulse_ranges]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def __post_init__(self) -> None:
        self.video_size = tuple(self.video_size)  # JSON round-trips tuples as lists
        self.servo_pulse_ranges = [tuple(r) for r in self.servo_pulse_ranges]
