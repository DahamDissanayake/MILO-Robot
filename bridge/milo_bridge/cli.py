"""Test/maintenance CLI (runs on the Pi against real hardware).

    python -m milo_bridge.cli pose wave
    python -m milo_bridge.cli face happy
    python -m milo_bridge.cli sweep
    python -m milo_bridge.cli paired
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from milo_common.auth import PairedStore

from .characterize import run_characterization
from .config import BridgeConfig
from .drivers.display import AnimMode, FaceDisplay
from .drivers.imu import Mpu6050
from .drivers.servos import SERVO_CHANNELS, ServoDriver
from .mcp.auth import mint_mcp_token
from .poses import POSES, PoseRunner

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets" / "faces"


def _hardware(cfg: BridgeConfig) -> tuple[ServoDriver, FaceDisplay]:
    servos = ServoDriver.from_hardware(pulse_ranges=cfg.servo_pulse_ranges, stagger_ms=cfg.servo_stagger_ms)
    display = FaceDisplay.from_hardware(ASSETS_DIR)
    return servos, display


async def _cmd_pose(cfg: BridgeConfig, name: str) -> None:
    servos, display = _hardware(cfg)
    await PoseRunner(servos, display).run(name)


async def _cmd_face(cfg: BridgeConfig, name: str) -> None:
    _, display = _hardware(cfg)
    await display.set_face(name, AnimMode.ONCE)
    await asyncio.sleep(3)


async def _cmd_sweep(cfg: BridgeConfig) -> None:
    servos, _ = _hardware(cfg)
    for name in SERVO_CHANNELS:
        for angle in (60, 120, 90):
            servos.set_angle(name, angle)
            await asyncio.sleep(0.6)


def _cmd_mcp_pair(cfg: BridgeConfig, name: str) -> None:
    store = PairedStore(cfg.paired_path)
    token_hex = mint_mcp_token(store, name)
    print(f"Paste this into the MCP client config for {name!r}:")
    print(f"  peer: {name}")
    print(f"  token: {token_hex}")


async def _cmd_characterize(cfg: BridgeConfig, pose: str | None, out: Path | None) -> None:
    servos, display = _hardware(cfg)
    runner = PoseRunner(servos, display)
    imu = Mpu6050.from_hardware()
    print("Calibrating IMU gyro bias — keep the robot still...")
    await asyncio.to_thread(imu.calibrate_gyro)
    from .gait.engine import GaitEngine

    gait = GaitEngine(servos, imu=imu, runner=runner)
    names = [pose] if pose else sorted(POSES)
    out_dir = out or (Path(cfg.data_dir) / "characterization" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    reports = await run_characterization(servos, imu, runner, gait, names, out_dir)
    for r in reports:
        flag = "OK" if r.safe else "UNSAFE"
        print(f"{r.name}: peak roll={r.peak_roll:.1f} peak pitch={r.peak_pitch:.1f} [{flag}]")
    print(f"Full report: {out_dir / 'report.md'}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="milo_bridge.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    pose = sub.add_parser("pose", help="run a scripted pose")
    pose.add_argument("name", choices=sorted(POSES))
    face = sub.add_parser("face", help="show a face")
    face.add_argument("name")
    sub.add_parser("sweep", help="sweep all servo channels")
    sub.add_parser("paired", help="list paired brains")
    mcp_pair = sub.add_parser("mcp-pair", help="mint an MCP bearer token for a human MCP client")
    mcp_pair.add_argument("--name", required=True, help="a name for this MCP client, e.g. your laptop")
    characterize = sub.add_parser("characterize", help="run every pose and record IMU response")
    characterize.add_argument("--pose", choices=sorted(POSES), help="characterize just one pose (default: all)")
    characterize.add_argument("--out", type=Path, help="output directory (default: ~/.milo/characterization/<timestamp>)")
    args = parser.parse_args(argv)

    cfg = BridgeConfig.load()
    if args.command == "pose":
        asyncio.run(_cmd_pose(cfg, args.name))
    elif args.command == "face":
        asyncio.run(_cmd_face(cfg, args.name))
    elif args.command == "sweep":
        asyncio.run(_cmd_sweep(cfg))
    elif args.command == "paired":
        store = PairedStore(cfg.paired_path)
        for peer_id in store.peer_ids():
            print(peer_id)
    elif args.command == "mcp-pair":
        _cmd_mcp_pair(cfg, args.name)
    elif args.command == "characterize":
        asyncio.run(_cmd_characterize(cfg, args.pose, args.out))


if __name__ == "__main__":
    main()
