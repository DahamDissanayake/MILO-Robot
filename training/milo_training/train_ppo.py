"""Train the gait policy with PPO (plan D.5).

    python -m milo_training.train_ppo --timesteps 20_000_000 --envs 16

Success bar in sim: tracks 0.1 m/s forward and +-30 deg/s yaw commands
without falling, across domain-randomization draws.
"""

from __future__ import annotations

import argparse
from pathlib import Path

RUNS_DIR = Path(__file__).resolve().parents[1] / "runs"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timesteps", type=int, default=20_000_000)
    parser.add_argument("--envs", type=int, default=16)
    parser.add_argument("--run-name", default="ppo-milo")
    parser.add_argument("--resume", type=Path, default=None, help="checkpoint .zip to continue")
    args = parser.parse_args(argv)

    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import CheckpointCallback
    from stable_baselines3.common.vec_env import SubprocVecEnv

    from .env import MiloEnv

    run_dir = RUNS_DIR / args.run_name
    run_dir.mkdir(parents=True, exist_ok=True)

    env = SubprocVecEnv([lambda: MiloEnv() for _ in range(args.envs)])
    if args.resume:
        model = PPO.load(args.resume, env=env)
    else:
        model = PPO(
            "MlpPolicy",
            env,
            policy_kwargs={"net_arch": [64, 64]},  # 2x64 MLP -> tiny ONNX for the Pi
            n_steps=512,
            batch_size=4096,
            learning_rate=3e-4,
            gamma=0.99,
            gae_lambda=0.95,
            ent_coef=0.005,
            verbose=1,
            tensorboard_log=str(run_dir / "tb"),
        )

    model.learn(
        total_timesteps=args.timesteps,
        callback=CheckpointCallback(
            save_freq=max(1, 1_000_000 // args.envs), save_path=str(run_dir), name_prefix="ckpt"
        ),
        progress_bar=True,
    )
    final = run_dir / "final.zip"
    model.save(final)
    print(f"saved {final} — export with: python -m milo_training.export_onnx {final}")


if __name__ == "__main__":
    main()
