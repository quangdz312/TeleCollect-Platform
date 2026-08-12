"""Demo chạy thử sim đơn giản: reset -> vài bước teleop giả -> lưu ảnh camera.

Dùng để xem trực quan sim mà không cần GUI (server không có màn hình):
render offscreen rồi lưu từng frame ra PNG.

Chạy:
    python scripts/sim_demo.py [output_dir]
"""

import sys
from pathlib import Path

import cv2
import numpy as np

from src.sim import kinematics, tasks
from src.sim.environment import RobotEnv


def main() -> None:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "data/sim_demo")
    out_dir.mkdir(parents=True, exist_ok=True)

    spec = tasks.get_task("lift_cube")
    env = RobotEnv(model_path=spec.model_path, task=spec.name, control_hz=30, image_size=256)

    obs = env.reset(seed=0)
    print(f"reset: qpos={[round(v, 3) for v in obs.qpos]}")

    # Chuyển động giả: hạ tay gắp xuống, kẹp lại, rồi nhấc lên.
    script = [{"dz": -0.03}] * 15 + [{"grip": 1.0}] * 5 + [{"dz": 0.03}] * 15

    for i, input_delta in enumerate(script):
        action = kinematics.teleop_input_to_action(input_delta, obs.qpos, scale=1.0)
        obs = env.step(action)

        frame = np.frombuffer(obs.images["agentview"], dtype=np.uint8).reshape(256, 256, 3)
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        cv2.imwrite(str(out_dir / f"frame_{i:03d}.png"), bgr)

    print(f"success={env.is_success()}")
    print(f"đã lưu {len(script)} frame vào {out_dir}/")
    env.close()


if __name__ == "__main__":
    main()
