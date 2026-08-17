"""Telemetry contract shared by the skill, the runner and the viewer.

The skill emits one StepTelemetry per control step. Nothing downstream may
assume anything beyond these fields.
"""
from dataclasses import dataclass, asdict, field
from typing import Optional, List

# Phases, in execution order. The viewer renders these as a progress strip.
PHASES = [
    "reach_grip",      # free space, move over the ceramic grip
    "descend_grip",    # approach down onto the grip
    "close_gripper",
    "lift",            # pick the frame off the table
    "upright",         # rotate the rod from flat to vertical
    "transport",       # carry to above the cavity
    "align",           # servo tip over the cavity mouth
    "insert",          # lower into the cavity
    "search",          # spiral / dither after a jam
    "release",
    "retreat",
    "reach_tool",
    "descend_tool",
    "close_tool",
    "lift_tool",
    "orient_tool",
    "transport_tool",
    "align_hole",
    "thread",
    "release_tool",
    "retreat_tool",
    "done",
]

FAILURE_KINDS = [
    "ik_unreachable",
    "grasp_missed",
    "grasp_slipped",
    "upright_failed",
    "align_timeout",
    "jammed",
    "knocked_stand",
    "dropped",
    "max_steps",
    "tool_grasp_missed",
    "tool_dropped",
    "hole_misaligned",
    "thread_failed",
    "knocked_frame",
    # Raised before any motion when neither ring face yields a wrist pose the arm
    # can hold, so the episode fails honestly instead of servoing into a stall.
    "no_holdable_face",
]


@dataclass
class StepTelemetry:
    step: int
    sim_time: float
    phase: str
    attempt: int
    # geometry, all in display units so the viewer never has to convert
    lateral_mm: float      # tip offset from the cavity axis
    tilt_deg: float        # rod off vertical
    yaw_err_deg: float     # folded into the rod's 90 deg symmetry
    depth_mm: float        # tip below the cavity mouth; negative while above
    # state
    grasped: bool
    n_contacts: int
    eef_pos: List[float]
    tip_pos: List[float]
    # thresholds, so the viewer can draw the bar without importing geometry
    capture_tol_mm: float
    seat_depth_mm: float
    stage: int = 1
    hole_dist_mm: float = float("nan")
    hole_tol_mm: float = float("nan")

    def to_dict(self):
        return asdict(self)


@dataclass
class EpisodeResult:
    seed: int
    success: bool                     # geometric truth (is_seated), not the env predicate
    env_predicate: bool               # what robosuite's _check_frame_assembled says
    failure: Optional[str] = None     # one of FAILURE_KINDS
    failed_phase: Optional[str] = None
    steps: int = 0
    wall_time_s: float = 0.0
    attempts: int = 1
    final_lateral_mm: float = float("nan")
    final_depth_mm: float = float("nan")
    trace: List[dict] = field(default_factory=list)
    stage: int = 1

    def to_dict(self):
        d = asdict(self)
        d["trace"] = self.trace
        return d