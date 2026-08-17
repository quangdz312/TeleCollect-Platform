"""Task-agnostic execution and raw transition capture for robot tools."""

from __future__ import annotations

from src.sim.collection.robomimic_hdf5_writer import EpisodeData
from src.sim.perturbations.runtime import PerturbationRuntime, validate_and_clip_action

from .base import RobotTool, ToolContext, ToolResult, ToolStatus


def execute_tool(
    tool: RobotTool,
    context: ToolContext,
    runtime: PerturbationRuntime | None = None,
) -> ToolResult:
    if isinstance(context.post_success_steps, bool) or context.post_success_steps < 0:
        raise ValueError("post_success_steps must be a non-negative integer")
    observation = context.reset(context.env, context.seed)
    if runtime is not None:
        runtime.reset_episode()
    tool.reset()
    if runtime is not None:
        set_variation = getattr(tool, "set_variation", None)
        if callable(set_variation):
            set_variation(runtime.variation)
    episode = EpisodeData(
        model_xml=context.env.sim.model.get_xml(),
        seed=context.seed,
        tool_name=tool.name,
        operator_version=tool.version,
    )
    status = ToolStatus.HORIZON
    reason = ToolStatus.HORIZON.value
    post_success_remaining: int | None = None

    # The horizon still caps time spent trying to solve the task. Once success
    # is observed, append a short tail so playback and training data show the
    # robot holding the completed state instead of ending on the first success
    # frame.
    max_steps = context.horizon + context.post_success_steps
    for step in range(max_steps):
        if post_success_remaining is None and step >= context.horizon:
            break
        state = context.env.sim.get_state().flatten().copy()
        operator_phase = tool.state
        policy_observation = (
            observation
            if runtime is None
            else runtime.policy_observation(observation, phase=operator_phase)
        )
        planned_action = tool.act(policy_observation)
        executed_action = (
            validate_and_clip_action(planned_action, context.env.action_spec)
            if runtime is None
            else runtime.apply_action(planned_action, step, phase=operator_phase)
        )
        next_observation, reward, done, _info = context.env.step(executed_action)
        success = context.is_success(context.env)
        episode.append(
            state=state,
            observation=context.adapt_observation(observation),
            action=executed_action,
            reward=reward,
            done=done,
            next_observation=context.adapt_observation(next_observation),
        )
        if context.verbose:
            context.logger(
                f"step={step + 1} tool={tool.name} state={tool.state} "
                f"reward={reward:.3f} success={success}"
            )
        observation = next_observation
        if success and post_success_remaining is None:
            status, reason = ToolStatus.SUCCESS, "success"
            post_success_remaining = context.post_success_steps
            if post_success_remaining == 0:
                break
        elif post_success_remaining is not None:
            post_success_remaining -= 1
            if post_success_remaining == 0:
                break
        if done:
            if post_success_remaining is None:
                status, reason = ToolStatus.ENVIRONMENT_DONE, "environment_done"
            break
        if post_success_remaining is not None:
            continue
        if tool.failed:
            status, reason = ToolStatus.FAILED, tool.failure_reason or "tool_failed"
            break
        if tool.finished:
            status, reason = ToolStatus.FAILED, "tool_finished_without_task_success"
            break

    episode.success = status == ToolStatus.SUCCESS
    episode.termination_reason = reason
    if episode.dones:
        episode.dones[-1] = True
    return ToolResult(tool.name, status, reason, len(episode.actions), tool.state, episode)
