# training — gait policy training

`milo-training` is the offline pipeline that produces the neural gait policy
the robot runs on-device: a MuJoCo simulation, PPO training, and export to
ONNX for the Pi's `bridge/milo_bridge/gait/` runner.

## What's in here

```
milo_training/
  env.py           Gymnasium environment for gait training — obs/action
                    contract mirrors bridge/milo_bridge/gait/policy.py
                    (OBS_VERSION 1): 30-dim observation, 8-dim normalized
                    delta action, firmware channel order
  train_ppo.py     trains the gait policy with PPO
                    (python -m milo_training.train_ppo --timesteps ... --envs ...)
  export_onnx.py   exports a trained SB3 PPO policy to ONNX for the Pi
                    (python -m milo_training.export_onnx runs/ppo-milo/final.zip policy.onnx)
models/
  milo.xml         MuJoCo model of the robot body used by env.py
```

## Install

```bash
pip install -e ./training          # base: env + ONNX contract tests
pip install -e "./training[full]"  # + mujoco, stable-baselines3, torch, tensorboard — GPU machine
```

## Training and deploying a policy

```bash
python -m milo_training.train_ppo --timesteps 20_000_000 --envs 16
python -m milo_training.export_onnx runs/ppo-milo/final.zip policy.onnx
```

Deploy: copy `policy.onnx` to the Pi (default `~/.milo/policy.onnx`); the
gait engine in `bridge/milo_bridge/gait/` loads it at startup.

## Tests

```bash
python -m pytest training/tests -v
```

The env/policy observation-action contract is tested without a GPU or a real
MuJoCo render loop.
