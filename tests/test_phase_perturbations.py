import numpy as np

from src.sim.perturbations.profiles import resolve_profile
from src.sim.perturbations.runtime import PerturbationRuntime

ACTION_SPEC = (np.full(7, -1.0), np.full(7, 1.0))


def runtime(task: str, quality: str, seed: int = 7) -> PerturbationRuntime:
    instance = PerturbationRuntime(
        resolve_profile(task, quality),
        ACTION_SPEC,
        base_seed=seed,
        episode_index=0,
        position_landmarks=("object",),
        orientation_landmarks=(),
    )
    instance.reset_episode()
    return instance


def test_clean_profile_stays_exactly_noise_free() -> None:
    instance = runtime("lift", "clean")
    observation = {"object": np.array([0.1, 0.2, 0.3])}

    assert instance.variation.is_zero
    assert instance.variation.fault_type == "none"
    assert instance.policy_observation(observation, phase="approach_cube") is observation
    np.testing.assert_array_equal(
        instance.apply_action(np.zeros(7), 1000, phase="approach_cube"),
        np.zeros(7),
    )


def test_action_noise_decays_to_zero_near_square_target() -> None:
    instance = runtime("square", "medium")
    planned = np.array([0.1, -0.1, 0.1, -0.1, 0.1, -0.1, 0.0])

    early = instance.apply_action(planned, 1000, phase="approach_nut")
    target = instance.apply_action(planned, 1001, phase="release")

    assert not np.allclose(early[:6], planned[:6])
    np.testing.assert_allclose(target, planned)
    assert instance.last_diagnostics is not None
    assert instance.last_diagnostics.phase_scale == 0.0


def test_perception_noise_is_scaled_by_phase() -> None:
    instance = runtime("can", "poor")
    observation = {"object": np.zeros(3)}

    early = instance.policy_observation(observation, phase="approach_can")["object"]
    target = instance.policy_observation(observation, phase="descend_into_bin")["object"]

    np.testing.assert_allclose(target, early * 0.20)


def test_lift_settle_phase_is_noise_free() -> None:
    instance = runtime("lift", "medium")
    observation = {"object": np.array([0.1, 0.2, 0.3])}
    planned = np.array([0.1, -0.1, 0.1, 0.0, 0.0, 0.0, -1.0])

    assert instance.policy_observation(
        observation, phase="settle_before_grasp",
    ) is observation
    np.testing.assert_array_equal(
        instance.apply_action(planned, 1000, phase="settle_before_grasp"), planned,
    )
    for phase in ("recover_align", "recover_descend"):
        assert instance.policy_observation(observation, phase=phase) is observation
        np.testing.assert_array_equal(
            instance.apply_action(planned, 1001, phase=phase), planned,
        )


def test_controlled_fault_is_deterministic_and_single() -> None:
    first = runtime("square", "poor", seed=31).variation
    second = runtime("square", "poor", seed=31).variation

    assert first == second
    assert first.fault_type in {
        "none", "grasp_offset", "target_offset", "yaw_error", "shallow_insert",
    }
    if first.fault_type == "none":
        assert first.fault_phase == ""
        assert first.fault_magnitude == 0.0
    else:
        assert first.fault_phase
        assert first.fault_magnitude > 0.0


def test_tool_hang_phase_decay_protects_precision_phases_and_gripper() -> None:
    instance = runtime("tool_hang", "medium")
    planned = np.array([0.2, -0.1, 0.15, -0.2, 0.1, -0.15, 1.0])

    stage1_early = instance.apply_action(planned, 1000, phase="reach_grip")
    stage1_insert = instance.apply_action(planned, 1001, phase="insert")
    stage2_early = instance.apply_action(planned, 1002, phase="home")
    stage2_thread = instance.apply_action(planned, 1003, phase="thread")

    assert not np.allclose(stage1_early[:6], planned[:6])
    assert not np.allclose(stage2_early[:6], planned[:6])
    np.testing.assert_array_equal(stage1_insert, planned)
    np.testing.assert_array_equal(stage2_thread, planned)
    assert stage1_early[6] == planned[6]
    assert stage2_early[6] == planned[6]


def test_tool_hang_candidate_has_no_semantic_or_gripper_faults() -> None:
    first = runtime("tool_hang", "poor", seed=31).variation
    second = runtime("tool_hang", "poor", seed=31).variation

    assert first == second
    assert first.fault_type == "none"
    assert first.fault_phase == ""
    assert first.fault_magnitude == 0.0
    assert first.schedule.gripper_close_steps == ()
    assert first.schedule.gripper_open_steps == ()


def test_tool_hang_clean_is_exact_identity_in_every_stage() -> None:
    instance = runtime("tool_hang", "clean")
    planned = np.array([0.2, -0.1, 0.15, -0.2, 0.1, -0.15, -1.0])

    for timestep, phase in enumerate(("reach_grip", "insert", "home", "thread")):
        np.testing.assert_array_equal(
            instance.apply_action(planned, timestep, phase=phase), planned,
        )


def test_servo_records_and_steps_the_transformed_action() -> None:
    from src.sim.skillgen.primitives import Servo

    class State:
        @staticmethod
        def flatten():
            return np.array([1.0, 2.0])

    class Sim:
        @staticmethod
        def get_state():
            return State()

    class Env:
        action_dim = 7
        sim = Sim()

        def __init__(self):
            self.stepped = None

        def step(self, action):
            self.stepped = np.asarray(action).copy()

    env = Env()
    servo = Servo.__new__(Servo)
    servo.env = env
    servo.pos_scale = np.ones(3)
    servo.rot_scale = np.ones(3)
    servo.record = []
    servo.action_transform = lambda action, timestep, *, phase: np.asarray(action) + np.array(
        [0.1, 0, 0, 0, 0, 0, 0],
    )
    servo.steps = 0
    servo.phase = "reach_grip"
    servo.prober = None
    servo.on_step = None

    executed = servo.act(dpos=np.array([0.2, 0.0, 0.0]), grip=1.0)

    np.testing.assert_array_equal(executed, env.stepped)
    np.testing.assert_array_equal(servo.record[0][1], executed)
    assert executed[6] == 1.0
