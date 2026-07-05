"""Export a trained SB3 PPO policy to ONNX for the Pi (plan D.6).

    python -m milo_training.export_onnx runs/ppo-milo/final.zip policy.onnx

Deploy: copy policy.onnx to the Pi (default ~/.milo/policy.onnx); the gait
engine picks it up on the next milo-bridge restart.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .env import ACTION_DIM, OBS_DIM


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("output", type=Path, nargs="?", default=Path("policy.onnx"))
    args = parser.parse_args(argv)

    import torch
    from stable_baselines3 import PPO

    model = PPO.load(args.checkpoint, device="cpu")

    class DeterministicActor(torch.nn.Module):
        """obs [1, OBS_DIM] -> mean action [1, ACTION_DIM], tanh-bounded."""

        def __init__(self, policy):
            super().__init__()
            self.extractor = policy.mlp_extractor
            self.action_net = policy.action_net

        def forward(self, obs: torch.Tensor) -> torch.Tensor:
            latent_pi = self.extractor.forward_actor(obs)
            return torch.tanh(self.action_net(latent_pi))

    actor = DeterministicActor(model.policy).eval()
    dummy = torch.zeros(1, OBS_DIM)
    torch.onnx.export(
        actor,
        dummy,
        str(args.output),
        input_names=["obs"],
        output_names=["action"],
        opset_version=17,
        dynamic_axes=None,  # fixed [1, N]: simplest + fastest for onnxruntime on the Pi
    )
    out = actor(dummy)
    assert out.shape == (1, ACTION_DIM)
    print(f"exported {args.output} (obs {OBS_DIM} -> action {ACTION_DIM})")


if __name__ == "__main__":
    main()
