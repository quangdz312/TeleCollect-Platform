# Teleop control audit against robosuite

## Round 1 summary - action vector

The teleop action vector path matches robosuite `OSC_POSE` / BASIC controller
conventions:

- The browser sends normalized motion axes in `[-1, 1]`.
- The backend keeps the order `[dx, dy, dz, drx, dry, drz, grip]`.
- `ControlLoop._clamp_action()` clamps the final action against `env.action_spec`
  before `env.step()`.
- Robosuite scales normalized pose input to the controller's configured
  translation and rotation deltas.
- Gripper sign is `+1` closed and `-1` open, which matches the current backend and
  frontend convention.

Round 1 therefore found no action-vector convention mismatch. The remaining issue
was the operator-facing keyboard map, not the backend pose/action path.

## Round 2 - keybinding audit against robosuite Keyboard

Ground truth was `.venv/Lib/site-packages/robosuite/devices/keyboard.py`, including
`on_press`, `on_release`, and `_display_controls`. The frontend key bindings now follow
that driver for the operator-facing keyboard controls:

| Key | Frontend command | Robosuite source behavior |
|---|---|---|
| ArrowUp | `linear[0] = -1` | decrement x |
| ArrowDown | `linear[0] = +1` | increment x |
| ArrowLeft | `linear[1] = -1` | decrement y |
| ArrowRight | `linear[1] = +1` | increment y |
| `.` | `linear[2] = -1` | decrement z |
| `;` | `linear[2] = +1` | increment z |
| `Y` | `angular[0] = +1` | `raw_drotation[0] += 0.1` |
| `H` | `angular[0] = -1` | `raw_drotation[0] -= 0.1` |
| `E` | `angular[1] = -1` | `raw_drotation[1] -= 0.1` |
| `R` | `angular[1] = +1` | `raw_drotation[1] += 0.1` |
| `P` | `angular[2] = +1` | `raw_drotation[2] += 0.1` |
| `O` | `angular[2] = -1` | `raw_drotation[2] -= 0.1` |
| Space | toggle gripper | toggle gripper on release |

The existing front-camera constraint still holds. The front camera looks back along
world `-x`, so the operator's "forward" key should command `-x`. Robosuite already maps
ArrowUp to decrement x, so no extra sign flip was needed for translation. The rotation
keys were copied from robosuite's `raw_drotation` signs rather than inferred from the
printed control labels.

`q` remains unbound in the frontend. Robosuite uses `q` for reset on key release, and
the backend has a `scene_reset` command, but `ControlLoop._reset_scene()` rejects reset
while recording (`InvalidSessionState`). Binding `q` inside `InputCollector` would lack
the session/recording context needed to make the keyboard action cleanly conditional and
would otherwise produce mid-take errors. The explicit "New scene" button remains the
safe reset path.

## Files checked

- `frontend/lib/teleop.ts`
- `frontend/lib/real-teleop.ts`
- `src/api/teleop.py`
- `src/core/session.py`
- `src/core/control_loop.py`
- `.venv/Lib/site-packages/robosuite/devices/keyboard.py`
- robosuite task success sources for Lift, PickPlaceCan, and NutAssemblySquare
