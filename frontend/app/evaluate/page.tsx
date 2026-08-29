"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Alert, Badge, Button, Card, Empty, Field, Input, Select, Stat } from "@/components/ui";
import {
  api,
  mediaUrl,
  type DatasetExport,
  type EvaluationRequest,
  type EvaluationRun,
  type RunStatus,
  type TrainingCheckpoint,
  type TrainingRun,
} from "@/lib/api";
import { EVALUATION_ENABLED } from "@/lib/features";
import { percent, timeAgo } from "@/lib/format";

const TONES: Record<RunStatus, "ok" | "warn" | "bad" | "info" | "neutral"> = {
  succeeded: "ok",
  running: "info",
  pending: "warn",
  failed: "bad",
  cancelled: "neutral",
};

const INITIAL_FORM: EvaluationRequest = {
  training_run_id: "",
  checkpoint_id: "",
  num_rollouts: 20,
  horizon: 250,
  seed: 5000,
  record_videos: 3,
};

type DetailTab = "summary" | "seeds" | "videos" | "log";
type VideoModalState = { evaluation: EvaluationRun; index: number };

function preferredCheckpoint(checkpoints: TrainingCheckpoint[]) {
  return checkpoints.find((item) => item.is_best_validation)
    ?? checkpoints.find((item) => item.is_latest)
    ?? checkpoints[0];
}

export default function EvaluatePage() {
  const { user } = useAuth();
  const [runs, setRuns] = useState<TrainingRun[]>([]);
  const [datasets, setDatasets] = useState<DatasetExport[]>([]);
  const [evaluations, setEvaluations] = useState<EvaluationRun[]>([]);
  const [form, setForm] = useState<EvaluationRequest>(INITIAL_FORM);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [detailTab, setDetailTab] = useState<DetailTab>("summary");
  const [videoModal, setVideoModal] = useState<VideoModalState | null>(null);
  const [log, setLog] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const eligibleRuns = useMemo(
    () => runs.filter((run) => run.status === "succeeded" && Boolean(run.checkpoints?.length)),
    [runs],
  );
  const selectedRun = eligibleRuns.find((run) => run.id === form.training_run_id) ?? null;
  const checkpoints = selectedRun?.checkpoints ?? [];
  const datasetById = useMemo(() => new Map(datasets.map((item) => [item.id, item])), [datasets]);
  const selectedDataset = selectedRun
    ? datasetById.get(selectedRun.dataset_id ?? selectedRun.config.dataset_id)
    : undefined;
  const selectedEvaluations = evaluations.filter((item) => item.training_run_id === form.training_run_id);

  const load = useCallback(async (preserveSelection = true) => {
    const [allRuns, allDatasets, allEvaluations] = await Promise.all([
      api.runs(),
      api.exports(),
      api.evaluations(),
    ]);
    const eligible = allRuns.filter((run) => run.status === "succeeded" && Boolean(run.checkpoints?.length));
    setRuns(allRuns);
    setDatasets(allDatasets);
    setEvaluations(allEvaluations);
    setForm((current) => {
      if (preserveSelection && eligible.some((run) => run.id === current.training_run_id)) return current;
      const query = new URLSearchParams(window.location.search);
      const requestedRun = eligible.find((run) => run.id === query.get("run"));
      const run = requestedRun ?? eligible[0];
      if (!run) return INITIAL_FORM;
      const requestedCheckpoint = run.checkpoints?.find((item) => item.id === query.get("checkpoint"));
      const checkpoint = requestedCheckpoint ?? preferredCheckpoint(run.checkpoints ?? []);
      return { ...current, training_run_id: run.id, checkpoint_id: checkpoint?.id ?? "" };
    });
  }, []);

  useEffect(() => {
    if (!user) return;
    void load(false).catch((problem) => setError(problem instanceof Error ? problem.message : "Could not load evaluations"));
  }, [load, user]);

  useEffect(() => {
    const active = evaluations.some((item) => item.status === "pending" || item.status === "running");
    if (!active) return;
    const timer = window.setInterval(() => void load(), 2500);
    return () => window.clearInterval(timer);
  }, [evaluations, load]);

  useEffect(() => {
    if (!expandedId || detailTab !== "log") {
      setLog("");
      return;
    }
    void api.evaluationLog(expandedId).then(setLog).catch(() => setLog(""));
  }, [detailTab, expandedId]);

  useEffect(() => {
    if (!videoModal) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setVideoModal(null);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [videoModal]);

  if (!user) return null;
  if (user.role === "operator") return <Alert tone="info">Evaluation is available to reviewers and administrators.</Alert>;

  function selectRun(runId: string) {
    const run = eligibleRuns.find((item) => item.id === runId);
    const checkpoint = preferredCheckpoint(run?.checkpoints ?? []);
    setForm((current) => ({ ...current, training_run_id: runId, checkpoint_id: checkpoint?.id ?? "" }));
  }

  async function startEvaluation() {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createEvaluation(form.training_run_id, form);
      setDetailTab("summary");
      setExpandedId(created.id);
      await load();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Could not start evaluation");
    } finally {
      setBusy(false);
    }
  }

  async function cancelEvaluation(id: string) {
    setBusy(true);
    setError(null);
    try {
      await api.cancelEvaluation(id);
      await load();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Could not cancel evaluation");
    } finally {
      setBusy(false);
    }
  }

  async function retryEvaluation(id: string) {
    setBusy(true);
    setError(null);
    try {
      const created = await api.retryEvaluation(id);
      setDetailTab("summary");
      setExpandedId(created.id);
      await load();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Could not retry evaluation");
    } finally {
      setBusy(false);
    }
  }

  async function deleteEvaluation(id: string) {
    if (!window.confirm("Delete this evaluation, including its log and rollout videos? This cannot be undone.")) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteEvaluation(id);
      if (expandedId === id) setExpandedId(null);
      await load();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Could not delete evaluation");
    } finally {
      setBusy(false);
    }
  }

  function toggleDetails(id: string) {
    if (expandedId === id) {
      setExpandedId(null);
      return;
    }
    setDetailTab("summary");
    setExpandedId(id);
  }

  const invalidVideos = form.record_videos > form.num_rollouts;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="font-heading text-[22px] font-bold tracking-tight">Evaluate checkpoints</h1>
        <p className="mt-0.5 text-sm text-ink-400">Run simulator rollouts and compare checkpoint success rates.</p>
      </div>

      {error && <Alert>{error}</Alert>}

      <Card title="New evaluation" subtitle="Selecting a checkpoint only prepares this form; evaluation starts after confirmation.">
        {eligibleRuns.length === 0 ? <Empty>Complete a training run with at least one checkpoint first.</Empty> : (
          <>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <Field label="Training run">
                <Select value={form.training_run_id} onChange={(event) => selectRun(event.target.value)}>
                  {eligibleRuns.map((run) => <option key={run.id} value={run.id}>{run.name}</option>)}
                </Select>
              </Field>
              <Field label="Checkpoint">
                <Select value={form.checkpoint_id} onChange={(event) => setForm({ ...form, checkpoint_id: event.target.value })}>
                  {checkpoints.map((checkpoint) => (
                    <option key={checkpoint.id} value={checkpoint.id}>
                      epoch {checkpoint.epoch}{checkpoint.is_best_validation ? " · best validation" : ""}{checkpoint.is_latest ? " · latest" : ""}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Task"><Input readOnly value={selectedDataset?.tasks.join(", ") || "Unknown task"} /></Field>
              <NumberField label="Rollouts" value={form.num_rollouts} min={1} onChange={(num_rollouts) => setForm({ ...form, num_rollouts })} />
              <NumberField label="Horizon" value={form.horizon ?? 250} min={1} onChange={(horizon) => setForm({ ...form, horizon })} />
              <NumberField label="Start seed" value={form.seed} min={0} onChange={(seed) => setForm({ ...form, seed })} />
              <NumberField label="Videos" value={form.record_videos} min={0} onChange={(record_videos) => setForm({ ...form, record_videos })} />
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <Button variant="primary" disabled={!EVALUATION_ENABLED || busy || !form.checkpoint_id || invalidVideos} onClick={() => void startEvaluation()}>
                {busy ? "Starting…" : "Run evaluation"}
              </Button>
              <span className="text-xs text-ink-400">Seeds {form.seed}–{form.seed + form.num_rollouts - 1}</span>
            </div>
            {!EVALUATION_ENABLED && (
              <div className="mt-3">
                <Alert tone="info">
                  This deployment does not run evaluations. Existing results stay
                  readable below.
                </Alert>
              </div>
            )}
            {invalidVideos && <div className="mt-3"><Alert>The video count cannot exceed the rollout count.</Alert></div>}
          </>
        )}
      </Card>

      <Card title="Checkpoint comparison" subtitle="Latest completed result for each checkpoint in this training run.">
        {selectedEvaluations.length === 0 ? <Empty>No evaluation results for this training run yet.</Empty> : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="text-left text-xs uppercase text-ink-400"><tr><th className="pb-2">Checkpoint</th><th className="pb-2">Status</th><th className="pb-2 text-right">Success rate</th><th className="pb-2 text-right">Rollouts</th><th className="pb-2 text-right">Mean steps</th><th className="pb-2 text-right">Created</th></tr></thead>
              <tbody>{latestByCheckpoint(selectedEvaluations).map((evaluation) => {
                const checkpoint = checkpoints.find((item) => item.id === evaluation.checkpoint_id);
                return <tr key={evaluation.id} className="border-t border-ink-700/60"><td className="py-2">{checkpoint ? `Epoch ${checkpoint.epoch}` : evaluation.checkpoint_id}</td><td className="py-2"><Badge tone={TONES[evaluation.status]}>{evaluation.status}</Badge></td><td className="py-2 text-right">{evaluation.success_rate == null ? "—" : percent(evaluation.success_rate, 1)}</td><td className="py-2 text-right">{evaluation.episodes.length}/{evaluation.num_episodes}</td><td className="py-2 text-right">{evaluation.mean_episode_length?.toFixed(1) ?? "—"}</td><td className="py-2 text-right text-xs text-ink-400">{timeAgo(evaluation.created_at)}</td></tr>;
              })}</tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Evaluation history">
        {selectedEvaluations.length === 0 ? <Empty>No evaluation has run for this training run yet.</Empty> : (
          <div className="space-y-2">
            {selectedEvaluations.map((evaluation) => {
              const checkpoint = checkpoints.find((item) => item.id === evaluation.checkpoint_id);
              const expanded = expandedId === evaluation.id;
              const successes = evaluation.episodes.filter((episode) => episode.success).length;
              return (
                <div key={evaluation.id} className="rounded-lg border border-ink-700/60 bg-ink-850/50 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div><div className="flex items-center gap-2"><strong>{checkpoint ? `Epoch ${checkpoint.epoch}` : evaluation.checkpoint_id}</strong><Badge tone={TONES[evaluation.status]}>{evaluation.status}</Badge></div><div className="mt-1 text-xs text-ink-400">{evaluation.task_name} · {timeAgo(evaluation.created_at)}</div></div>
                    <div className="flex flex-wrap items-center justify-end gap-2">
                      {evaluation.success_rate != null && <div className="mr-1 text-right"><div className="text-lg font-semibold text-ok-400">{percent(evaluation.success_rate, 1)}</div><div className="text-[11px] text-ink-400">{successes}/{evaluation.episodes.length} successful</div></div>}
                      {(evaluation.status === "pending" || evaluation.status === "running") ? (
                        <Button variant="danger" disabled={busy} onClick={() => void cancelEvaluation(evaluation.id)}>Cancel</Button>
                      ) : (
                        <>
                          {(evaluation.status === "failed" || evaluation.status === "cancelled") && <Button variant="primary" disabled={busy} onClick={() => void retryEvaluation(evaluation.id)}>Retry</Button>}
                          <Button variant="danger" disabled={busy} onClick={() => void deleteEvaluation(evaluation.id)}>Delete</Button>
                        </>
                      )}
                      <Button variant="subtle" onClick={() => toggleDetails(evaluation.id)}>{expanded ? "Hide" : "View details"}</Button>
                    </div>
                  </div>
                  {expanded && (
                    <div className="mt-3 border-t border-ink-700/60 pt-3">
                      <DetailTabs value={detailTab} onChange={setDetailTab} />
                      {detailTab === "summary" && <EvaluationSummary evaluation={evaluation} />}
                      {detailTab === "seeds" && <SeedResults evaluation={evaluation} />}
                      {detailTab === "videos" && <EvaluationVideos evaluation={evaluation} onOpen={(index) => setVideoModal({ evaluation, index })} />}
                      {detailTab === "log" && (log ? <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-ink-700 bg-ink-950 p-3 text-[11px] text-ink-300">{log}</pre> : <Empty>No log available.</Empty>)}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>
      {videoModal && <VideoModal state={videoModal} onChange={setVideoModal} onClose={() => setVideoModal(null)} />}
    </div>
  );
}

function latestByCheckpoint(items: EvaluationRun[]) {
  const latest = new Map<string, EvaluationRun>();
  for (const item of items) {
    if (!latest.has(item.checkpoint_id)) latest.set(item.checkpoint_id, item);
  }
  return Array.from(latest.values());
}

function NumberField({ label, value, min, onChange }: { label: string; value: number; min: number; onChange: (value: number) => void }) {
  return <Field label={label}><Input type="number" min={min} value={value} onChange={(event) => onChange(Number(event.target.value))} /></Field>;
}

function DetailTabs({ value, onChange }: { value: DetailTab; onChange: (value: DetailTab) => void }) {
  const tabs: { value: DetailTab; label: string }[] = [
    { value: "summary", label: "Summary" },
    { value: "seeds", label: "Seeds" },
    { value: "videos", label: "Videos" },
    { value: "log", label: "Log" },
  ];
  return <div className="mb-3 flex flex-wrap gap-1 border-b border-ink-700/60">{tabs.map((tab) => <button key={tab.value} type="button" onClick={() => onChange(tab.value)} className={`border-b-2 px-3 py-2 text-xs font-medium ${value === tab.value ? "border-accent-500 text-accent-500" : "border-transparent text-ink-400 hover:text-ink-200"}`}>{tab.label}</button>)}</div>;
}

function EvaluationSummary({ evaluation }: { evaluation: EvaluationRun }) {
  const successes = evaluation.episodes.filter((episode) => episode.success).length;
  const failures = evaluation.episodes.filter((episode) => !episode.success);
  return <div className="space-y-3">
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Stat label="Success rate" value={evaluation.success_rate == null ? "—" : percent(evaluation.success_rate, 1)} />
      <Stat label="Successful" value={`${successes} / ${evaluation.episodes.length}`} />
      <Stat label="Mean steps" value={evaluation.mean_episode_length?.toFixed(1) ?? "—"} />
      <Stat label="Recorded videos" value={evaluation.episodes.filter((episode) => episode.video).length.toString()} />
    </div>
    {failures.length > 0 && <div><div className="mb-1.5 text-xs font-medium text-ink-300">Failed seeds</div><div className="flex flex-wrap gap-1.5">{failures.map((episode) => <span key={episode.seed} className="rounded bg-bad-600/20 px-2 py-1 text-[11px] text-bad-400">{episode.seed} · {episode.steps} steps</span>)}</div></div>}
    {evaluation.error && <Alert><pre className="whitespace-pre-wrap text-xs">{evaluation.error}</pre></Alert>}
  </div>;
}

function SeedResults({ evaluation }: { evaluation: EvaluationRun }) {
  if (evaluation.episodes.length === 0) return <Empty>No completed rollouts yet.</Empty>;
  return <div className="flex max-h-72 flex-wrap gap-1.5 overflow-auto pr-1">{evaluation.episodes.map((episode) => <span key={episode.seed} title={`${episode.steps} steps`} className={`rounded px-2 py-1 text-[11px] ${episode.success ? "bg-ok-600/20 text-ok-400" : "bg-bad-600/20 text-bad-400"}`}>{episode.seed}: {episode.success ? "success" : "fail"}</span>)}</div>;
}

function EvaluationVideos({ evaluation, onOpen }: { evaluation: EvaluationRun; onOpen: (index: number) => void }) {
  const recorded = evaluation.episodes.filter((episode) => episode.video);
  if (recorded.length === 0) return <Empty>No videos were recorded for this evaluation.</Empty>;
  return <div className="flex gap-3 overflow-x-auto pb-2">{recorded.map((episode, index) => <button type="button" key={episode.seed} onClick={() => onOpen(index)} className="w-56 shrink-0 overflow-hidden rounded-lg border border-ink-700 bg-ink-900 text-left transition hover:border-accent-500/70"><video className="pointer-events-none h-32 w-full bg-black object-cover" muted preload="metadata" src={mediaUrl(`/training/evaluations/${evaluation.id}/videos/${episode.video}`)} /><div className="flex items-center justify-between gap-2 px-2.5 py-2"><span className="text-xs font-medium">Seed {episode.seed}</span><Badge tone={episode.success ? "ok" : "bad"}>{episode.success ? "success" : "fail"}</Badge></div><div className="px-2.5 pb-2 text-[11px] text-ink-400">{episode.steps} steps · click to play</div></button>)}</div>;
}

function VideoModal({ state, onChange, onClose }: { state: VideoModalState; onChange: (state: VideoModalState) => void; onClose: () => void }) {
  const recorded = state.evaluation.episodes.filter((episode) => episode.video);
  const episode = recorded[state.index];
  if (!episode?.video) return null;
  const move = (offset: number) => onChange({ ...state, index: (state.index + offset + recorded.length) % recorded.length });
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" role="dialog" aria-modal="true" aria-label={`Evaluation video seed ${episode.seed}`} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><div className="w-full max-w-5xl rounded-xl border border-ink-700 bg-ink-900 p-4 shadow-2xl"><div className="mb-3 flex flex-wrap items-center justify-between gap-3"><div><div className="flex items-center gap-2"><strong>Seed {episode.seed}</strong><Badge tone={episode.success ? "ok" : "bad"}>{episode.success ? "success" : "fail"}</Badge></div><div className="mt-1 text-xs text-ink-400">{episode.steps} steps · video {state.index + 1} of {recorded.length}</div></div><Button variant="subtle" onClick={onClose}>Close</Button></div><video key={episode.video} className="max-h-[70vh] w-full rounded-lg bg-black" controls autoPlay preload="auto" src={mediaUrl(`/training/evaluations/${state.evaluation.id}/videos/${episode.video}`)} /><div className="mt-3 flex justify-between"><Button variant="subtle" onClick={() => move(-1)}>← Previous</Button><Button variant="subtle" onClick={() => move(1)}>Next →</Button></div></div></div>;
}
