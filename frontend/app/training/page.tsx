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
  type TrainingRequest,
  type TrainingRun,
} from "@/lib/api";
import { bytes, percent, timeAgo } from "@/lib/format";

const TONES: Record<RunStatus, "ok" | "warn" | "bad" | "info" | "neutral"> = {
  succeeded: "ok",
  running: "info",
  pending: "warn",
  failed: "bad",
  cancelled: "neutral",
};

const INITIAL_FORM: TrainingRequest = {
  dataset_id: "",
  name: "bc-v1",
  policy: "bc",
  epochs: 200,
  batch_size: 32,
  num_workers: 0,
  device: "auto",
  learning_rate: 0.0001,
  seed: 1,
  save_every_n_epochs: 20,
  sequence_length: 50,
  rnn_hidden_dim: 400,
  rnn_layers: 2,
  normalize_observations: true,
  observation_profile: "minimal",
  rollout_enabled: true,
  rollout_every_n_epochs: 20,
  rollout_episodes: 5,
  rollout_horizon: 500,
};

const RUN_PREFERENCES_KEY = "training-run-preferences-v1";

type RunPreferences = {
  archived: string[];
  pinned: string[];
};

export default function TrainingPage() {
  const { user } = useAuth();
  const [datasets, setDatasets] = useState<DatasetExport[]>([]);
  const [runs, setRuns] = useState<TrainingRun[]>([]);
  const [evaluations, setEvaluations] = useState<EvaluationRun[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<TrainingRequest>(INITIAL_FORM);
  const [log, setLog] = useState("");
  const [evaluationLog, setEvaluationLog] = useState("");
  const [selectedTask, setSelectedTask] = useState("all");
  const [runView, setRunView] = useState<"active" | "archived" | "all">("active");
  const [preferences, setPreferences] = useState<RunPreferences>({ archived: [], pinned: [] });
  const [showAllCheckpoints, setShowAllCheckpoints] = useState(false);
  const [expandedEvaluationId, setExpandedEvaluationId] = useState<string | null>(null);
  const [showAllEvaluations, setShowAllEvaluations] = useState(false);
  const [showJobLog, setShowJobLog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [evaluationForm, setEvaluationForm] = useState<EvaluationRequest>({
    training_run_id: "",
    checkpoint_id: "",
    num_rollouts: 20,
    horizon: 250,
    seed: 5000,
    record_videos: 3,
  });

  const canTrain = user?.role === "reviewer" || user?.role === "admin";
  const datasetById = useMemo(
    () => new Map(datasets.map((dataset) => [dataset.id, dataset])),
    [datasets],
  );
  const taskForRun = useCallback(
    (run: TrainingRun) => datasetById.get(run.dataset_id ?? run.config.dataset_id)?.tasks?.[0] ?? "Unknown task",
    [datasetById],
  );
  const taskNames = useMemo(
    () => Array.from(new Set(runs.map(taskForRun))).sort((a, b) => a.localeCompare(b)),
    [runs, taskForRun],
  );
  const visibleRuns = useMemo(() => {
    const archived = new Set(preferences.archived);
    const pinned = new Set(preferences.pinned);
    return runs
      .filter((run) => selectedTask === "all" || taskForRun(run) === selectedTask)
      .filter((run) => runView === "all" || (runView === "archived" ? archived.has(run.id) : !archived.has(run.id)))
      .sort((a, b) => Number(pinned.has(b.id)) - Number(pinned.has(a.id)));
  }, [preferences, runView, runs, selectedTask, taskForRun]);
  const selected = useMemo(
    () => runs.find((run) => run.id === selectedId) ?? null,
    [runs, selectedId],
  );
  const selectedEvaluations = useMemo(
    () => evaluations.filter((item) => item.training_run_id === selectedId),
    [evaluations, selectedId],
  );
  const latestEvaluation = selectedEvaluations[0] ?? null;
  const compactCheckpoints = useMemo(() => {
    const checkpoints = selected?.checkpoints ?? [];
    if (showAllCheckpoints) return checkpoints;
    const important = checkpoints.filter(
      (checkpoint) => checkpoint.is_best_validation || checkpoint.is_latest || checkpoint.id === evaluationForm.checkpoint_id,
    );
    return Array.from(new Map(important.map((checkpoint) => [checkpoint.id, checkpoint])).values());
  }, [evaluationForm.checkpoint_id, selected?.checkpoints, showAllCheckpoints]);
  const visibleEvaluations = showAllEvaluations ? selectedEvaluations : selectedEvaluations.slice(0, 5);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(RUN_PREFERENCES_KEY);
      if (stored) setPreferences(JSON.parse(stored) as RunPreferences);
    } catch {
      // Corrupt browser preferences should never prevent the page from loading.
    }
  }, []);

  const updatePreferences = useCallback((next: RunPreferences) => {
    setPreferences(next);
    window.localStorage.setItem(RUN_PREFERENCES_KEY, JSON.stringify(next));
  }, []);

  const load = useCallback(async () => {
    const [allDatasets, allRuns, allEvaluations] = await Promise.all([
      api.exports(),
      api.runs(),
      api.evaluations(),
    ]);
    const ready = allDatasets.filter(
      (item) => item.format === "robomimic" && item.status === "ready",
    );
    setDatasets(ready);
    setRuns(allRuns);
    setEvaluations(allEvaluations);
    setSelectedId((current) => current ?? allRuns[0]?.id ?? null);
    setForm((current) =>
      current.dataset_id || !ready.length ? current : { ...current, dataset_id: ready[0].id },
    );
  }, []);

  useEffect(() => {
    if (!user) return;
    void load().catch((exc) =>
      setError(exc instanceof Error ? exc.message : "Không tải được dữ liệu training"),
    );
  }, [load, user]);

  useEffect(() => {
    const active =
      runs.some((run) => run.status === "pending" || run.status === "running") ||
      evaluations.some((item) => item.status === "pending" || item.status === "running");
    if (!active) return;
    const timer = window.setInterval(() => void load(), 2500);
    return () => window.clearInterval(timer);
  }, [evaluations, load, runs]);

  useEffect(() => {
    if (!selected) return;
    const checkpoints = selected.checkpoints ?? [];
    const preferred =
      checkpoints.find((checkpoint) => checkpoint.is_best_validation) ??
      checkpoints.find((checkpoint) => checkpoint.is_latest) ??
      checkpoints[0];
    setEvaluationForm((current) => ({
      ...current,
      training_run_id: selected.id,
      checkpoint_id: preferred?.id ?? "",
    }));
  }, [selected?.id, selected?.status]);

  useEffect(() => {
    setShowAllCheckpoints(false);
    setExpandedEvaluationId(null);
    setShowAllEvaluations(false);
    setShowJobLog(false);
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId) {
      setLog("");
      return;
    }
    let disposed = false;
    const refreshLog = async () => {
      try {
        const value = await api.runLog(selectedId);
        if (!disposed) setLog(value);
      } catch {
        if (!disposed) setLog("");
      }
    };
    void refreshLog();
    if (selected?.status !== "pending" && selected?.status !== "running") {
      return () => { disposed = true; };
    }
    const timer = window.setInterval(() => void refreshLog(), 2500);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [selected?.status, selectedId]);

  useEffect(() => {
    if (!latestEvaluation) {
      setEvaluationLog("");
      return;
    }
    let disposed = false;
    const refresh = async () => {
      try {
        const value = await api.evaluationLog(latestEvaluation.id);
        if (!disposed) setEvaluationLog(value);
      } catch {
        if (!disposed) setEvaluationLog("");
      }
    };
    void refresh();
    if (latestEvaluation.status !== "pending" && latestEvaluation.status !== "running") {
      return () => { disposed = true; };
    }
    const timer = window.setInterval(() => void refresh(), 2500);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [latestEvaluation?.id, latestEvaluation?.status]);

  if (!user) return null;

  async function startTraining() {
    setBusy(true);
    setError(null);
    try {
      const run = await api.createRun(form);
      setSelectedId(run.id);
      const task = datasetById.get(form.dataset_id)?.tasks?.[0];
      if (task) setSelectedTask(task);
      setRunView("active");
      await load();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Không thể bắt đầu training");
    } finally {
      setBusy(false);
    }
  }

  async function cancelTraining() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await api.cancelRun(selected.id);
      await load();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Không thể hủy training");
    } finally {
      setBusy(false);
    }
  }

  async function startEvaluation() {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await api.createEvaluation(selected.id, {
        ...evaluationForm,
        training_run_id: selected.id,
      });
      await load();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Không thể bắt đầu evaluation");
    } finally {
      setBusy(false);
    }
  }

  async function cancelEvaluation(evaluationId: string) {
    setBusy(true);
    setError(null);
    try {
      await api.cancelEvaluation(evaluationId);
      await load();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Không thể hủy evaluation");
    } finally {
      setBusy(false);
    }
  }

  function selectTask(task: string) {
    setSelectedTask(task);
    const next = runs.find((run) => {
      const matchesTask = task === "all" || taskForRun(run) === task;
      return matchesTask && !preferences.archived.includes(run.id);
    });
    setRunView("active");
    setSelectedId(next?.id ?? null);
  }

  function toggleArchived(runId: string) {
    const isArchived = preferences.archived.includes(runId);
    updatePreferences({
      ...preferences,
      archived: isArchived
        ? preferences.archived.filter((id) => id !== runId)
        : [...preferences.archived, runId],
    });
    if (!isArchived && runView === "active") {
      const next = visibleRuns.find((run) => run.id !== runId);
      setSelectedId(next?.id ?? null);
    }
  }

  function togglePinned(runId: string) {
    const isPinned = preferences.pinned.includes(runId);
    updatePreferences({
      ...preferences,
      pinned: isPinned
        ? preferences.pinned.filter((id) => id !== runId)
        : [...preferences.pinned, runId],
    });
  }

  const totalEpochs = Number(selected?.config.epochs ?? 0);
  const currentEpoch = selected?.epoch ?? 0;
  const progress = totalEpochs ? Math.min(100, (currentEpoch / totalEpochs) * 100) : 0;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="font-heading text-[22px] font-bold tracking-tight">Imitation learning</h1>
        <p className="mt-0.5 text-sm text-ink-400">
          Huấn luyện BC hoặc BC-RNN từ dataset HDF5 đã duyệt và theo dõi checkpoint.
        </p>
      </div>

      {error && <Alert>{error}</Alert>}

      {canTrain && (
        <Card title="New training run" subtitle="Mỗi thời điểm backend chỉ chạy một job để tránh tranh GPU.">
          {datasets.length === 0 ? (
            <Empty>Hãy export ít nhất một RoboMimic dataset ở trạng thái ready.</Empty>
          ) : (
            <>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Field label="Run name">
                  <Input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
                </Field>
                <Field label="Dataset">
                  <Select value={form.dataset_id} onChange={(event) => setForm({ ...form, dataset_id: event.target.value })}>
                    {datasets.map((dataset) => (
                      <option key={dataset.id} value={dataset.id}>
                        {dataset.name} · {dataset.num_episodes} episodes
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="Policy">
                  <Select value={form.policy} onChange={(event) => setForm({ ...form, policy: event.target.value as "bc" | "bc-rnn" })}>
                    <option value="bc">BC</option>
                    <option value="bc-rnn">BC-RNN (LSTM)</option>
                  </Select>
                </Field>
                <Field label="Device">
                  <Select value={form.device} onChange={(event) => setForm({ ...form, device: event.target.value as TrainingRequest["device"] })}>
                    <option value="auto">Auto</option>
                    <option value="cuda">CUDA GPU</option>
                    <option value="cpu">CPU</option>
                  </Select>
                </Field>
                <NumberField label="Epochs" value={form.epochs} min={1} onChange={(epochs) => setForm({ ...form, epochs })} />
                <NumberField label="Batch size" value={form.batch_size} min={1} onChange={(batch_size) => setForm({ ...form, batch_size })} />
                <NumberField label="Workers" value={form.num_workers} min={0} onChange={(num_workers) => setForm({ ...form, num_workers })} />
                <Field label="Learning rate">
                  <Input type="number" min="0.0000001" step="0.00001" value={form.learning_rate} onChange={(event) => setForm({ ...form, learning_rate: Number(event.target.value) })} />
                </Field>
                <NumberField label="Seed" value={form.seed} min={0} onChange={(seed) => setForm({ ...form, seed })} />
                <NumberField label="Save every N epochs" value={form.save_every_n_epochs ?? 1} min={1} onChange={(save_every_n_epochs) => setForm({ ...form, save_every_n_epochs })} />
                {form.policy === "bc-rnn" && (
                  <>
                    <NumberField label="Sequence length" value={form.sequence_length} min={1} onChange={(sequence_length) => setForm({ ...form, sequence_length })} />
                    <NumberField label="RNN hidden dim" value={form.rnn_hidden_dim} min={1} onChange={(rnn_hidden_dim) => setForm({ ...form, rnn_hidden_dim })} />
                    <NumberField label="RNN layers" value={form.rnn_layers} min={1} onChange={(rnn_layers) => setForm({ ...form, rnn_layers })} />
                  </>
                )}
                <Field label="Observation profile">
                  <Select value={form.observation_profile} onChange={(event) => setForm({ ...form, observation_profile: event.target.value as TrainingRequest["observation_profile"] })}>
                    <option value="minimal">Minimal task state</option>
                    <option value="all">All dataset observations</option>
                  </Select>
                </Field>
                <Field label="Normalize observations">
                  <Select value={form.normalize_observations ? "yes" : "no"} onChange={(event) => setForm({ ...form, normalize_observations: event.target.value === "yes" })}>
                    <option value="yes">Enabled</option>
                    <option value="no">Disabled (keeps validation loss)</option>
                  </Select>
                </Field>
                <Field label="Training rollouts">
                  <Select value={form.rollout_enabled ? "yes" : "no"} onChange={(event) => setForm({ ...form, rollout_enabled: event.target.value === "yes" })}>
                    <option value="yes">Enabled</option>
                    <option value="no">Disabled</option>
                  </Select>
                </Field>
                {form.rollout_enabled && (
                  <>
                    <NumberField label="Rollout every N epochs" value={form.rollout_every_n_epochs} min={1} onChange={(rollout_every_n_epochs) => setForm({ ...form, rollout_every_n_epochs })} />
                    <NumberField label="Rollouts per check" value={form.rollout_episodes} min={1} onChange={(rollout_episodes) => setForm({ ...form, rollout_episodes })} />
                    <NumberField label="Training rollout horizon" value={form.rollout_horizon} min={1} onChange={(rollout_horizon) => setForm({ ...form, rollout_horizon })} />
                  </>
                )}
              </div>
              {form.normalize_observations && (
                <div className="mt-3"><Alert tone="info">RoboMimic không hỗ trợ normalization cùng validation split. Run này sẽ chọn checkpoint bằng simulator rollout success thay cho validation loss.</Alert></div>
              )}
              <div className="mt-4 flex items-center gap-3">
                <Button variant="primary" disabled={busy || !form.dataset_id || !form.name.trim()} onClick={() => void startTraining()}>
                  {busy ? "Starting…" : "Start training"}
                </Button>
                <span className="text-xs text-ink-400">Training tiếp tục ở backend nếu bạn chuyển tab.</span>
              </div>
            </>
          )}
        </Card>
      )}

      <Card title="Training workspace" subtitle="Chọn task và run cần xem; các run lưu trữ vẫn có thể mở lại bất cứ lúc nào.">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Task">
            <Select value={selectedTask} onChange={(event) => selectTask(event.target.value)}>
              <option value="all">All tasks</option>
              {taskNames.map((task) => <option key={task} value={task}>{task}</option>)}
            </Select>
          </Field>
          <Field label="Runs">
            <Select value={runView} onChange={(event) => setRunView(event.target.value as typeof runView)}>
              <option value="active">Active</option>
              <option value="archived">Archived</option>
              <option value="all">All</option>
            </Select>
          </Field>
          <Field label="Training run">
            <Select value={selectedId ?? ""} onChange={(event) => setSelectedId(event.target.value || null)}>
              <option value="">Select a run</option>
              {visibleRuns.map((run) => (
                <option key={run.id} value={run.id}>
                  {preferences.pinned.includes(run.id) ? "★ " : ""}{run.name} · {run.status}
                </option>
              ))}
            </Select>
          </Field>
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-[320px_minmax(0,1fr)]">
        <Card title="Training runs">
          {visibleRuns.length === 0 ? <Empty>Không có training run phù hợp bộ lọc.</Empty> : (
            <ul className="space-y-2">
              {visibleRuns.map((run) => (
                <li key={run.id}>
                  <button onClick={() => setSelectedId(run.id)} className={`w-full rounded-lg border px-3 py-2 text-left ${selectedId === run.id ? "border-accent-500/60 bg-ink-850" : "border-ink-700/50 hover:bg-ink-850"}`}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium">{preferences.pinned.includes(run.id) ? "★ " : ""}{run.name}</span>
                      <Badge tone={TONES[run.status]}>{run.status}</Badge>
                    </div>
                    <div className="mt-1 text-xs text-ink-400">{taskForRun(run)} · {String(run.config.policy).toUpperCase()} · {timeAgo(run.created_at)}</div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {!selected ? <Empty>Chọn một training run để xem chi tiết.</Empty> : (
          <div className="space-y-5">
            <Card title={selected.name} subtitle={`${taskForRun(selected)} · ${String(selected.config.policy).toUpperCase()} · ${totalEpochs} epochs`} actions={<div className="flex flex-wrap gap-2"><Badge tone={TONES[selected.status]}>{selected.status}</Badge><Button variant="subtle" onClick={() => togglePinned(selected.id)}>{preferences.pinned.includes(selected.id) ? "Unpin" : "Pin"}</Button><Button variant="subtle" onClick={() => toggleArchived(selected.id)}>{preferences.archived.includes(selected.id) ? "Restore" : "Archive"}</Button>{canTrain && (selected.status === "running" || selected.status === "pending") && <Button variant="danger" disabled={busy} onClick={() => void cancelTraining()}>Cancel</Button>}</div>}>
              <div className="grid gap-3 sm:grid-cols-3">
                <Stat label="Epoch" value={`${currentEpoch} / ${totalEpochs}`} />
                <Stat label="Train loss" value={selected.train_loss == null ? "—" : selected.train_loss.toFixed(6)} />
                <Stat label="Validation loss" value={selected.validation_loss == null ? "—" : selected.validation_loss.toFixed(6)} />
              </div>
              <div className="mt-4 h-2 overflow-hidden rounded-full bg-ink-800"><div className="h-full bg-accent-500 transition-all" style={{ width: `${progress}%` }} /></div>
              <div className="mt-1 text-right text-xs tabular text-ink-400">{progress.toFixed(1)}%</div>
              {selected.error && <div className="mt-3"><Alert><pre className="whitespace-pre-wrap text-xs">{selected.error}</pre></Alert></div>}
            </Card>

            <Card title="Checkpoints" subtitle="Best validation là checkpoint có validation loss thấp nhất.">
              {!selected.checkpoints?.length ? <Empty>Chưa có checkpoint.</Empty> : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="text-xs uppercase text-ink-400"><tr><th className="px-2 py-2">Epoch</th><th className="px-2 py-2">Validation loss</th><th className="px-2 py-2">Size</th><th className="px-2 py-2">File</th><th className="px-2 py-2">Tags</th></tr></thead>
                    <tbody className="divide-y divide-ink-700/60">
                      {compactCheckpoints.map((checkpoint) => (
                        <tr key={checkpoint.id}><td className="px-2 py-2 tabular">{checkpoint.epoch}</td><td className="px-2 py-2 tabular">{checkpoint.validation_loss == null ? "—" : checkpoint.validation_loss.toFixed(8)}</td><td className="px-2 py-2">{bytes(checkpoint.size_bytes)}</td><td className="max-w-72 truncate px-2 py-2 font-mono text-xs text-ink-300" title={checkpoint.filename}>{checkpoint.filename}</td><td className="px-2 py-2"><div className="flex gap-1">{checkpoint.is_best_validation && <Badge tone="ok">best validation</Badge>}{checkpoint.is_latest && <Badge tone="info">latest</Badge>}</div></td></tr>
                      ))}
                    </tbody>
                  </table>
                  {selected.checkpoints.length > compactCheckpoints.length && (
                    <div className="mt-3 text-center"><Button variant="subtle" onClick={() => setShowAllCheckpoints(true)}>View all checkpoints ({selected.checkpoints.length})</Button></div>
                  )}
                  {showAllCheckpoints && selected.checkpoints.length > 3 && (
                    <div className="mt-3 text-center"><Button variant="subtle" onClick={() => setShowAllCheckpoints(false)}>Show important only</Button></div>
                  )}
                </div>
              )}
            </Card>

            {selected.status === "succeeded" && Boolean(selected.checkpoints?.length) && (
              <Card title="Evaluate in simulator" subtitle="Mỗi rollout dùng một seed khác nhau; mặc định chọn best validation.">
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                  <Field label="Checkpoint">
                    <Select value={evaluationForm.checkpoint_id} onChange={(event) => setEvaluationForm({ ...evaluationForm, checkpoint_id: event.target.value })}>
                      {selected.checkpoints?.map((checkpoint) => (
                        <option key={checkpoint.id} value={checkpoint.id}>
                          epoch {checkpoint.epoch}
                          {checkpoint.is_best_validation ? " · best validation" : ""}
                          {checkpoint.is_latest ? " · latest" : ""}
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <NumberField label="Rollouts" value={evaluationForm.num_rollouts} min={1} onChange={(num_rollouts) => setEvaluationForm({ ...evaluationForm, num_rollouts })} />
                  <NumberField label="Horizon" value={evaluationForm.horizon ?? 250} min={1} onChange={(horizon) => setEvaluationForm({ ...evaluationForm, horizon })} />
                  <NumberField label="Start seed" value={evaluationForm.seed} min={0} onChange={(seed) => setEvaluationForm({ ...evaluationForm, seed })} />
                  <NumberField label="Videos" value={evaluationForm.record_videos} min={0} onChange={(record_videos) => setEvaluationForm({ ...evaluationForm, record_videos })} />
                </div>
                <div className="mt-4 flex items-center gap-3">
                  <Button
                    variant="primary"
                    disabled={
                      busy ||
                      !evaluationForm.checkpoint_id ||
                      evaluationForm.record_videos > evaluationForm.num_rollouts
                    }
                    onClick={() => void startEvaluation()}
                  >
                    Run evaluation
                  </Button>
                  <span className="text-xs text-ink-400">
                    Seeds {evaluationForm.seed}–{evaluationForm.seed + evaluationForm.num_rollouts - 1}
                  </span>
                </div>
                {evaluationForm.record_videos > evaluationForm.num_rollouts && (
                  <div className="mt-3"><Alert>Số video không được lớn hơn số rollout.</Alert></div>
                )}
              </Card>
            )}

            <Card title="Evaluation results">
              {selectedEvaluations.length === 0 ? <Empty>Chưa chạy evaluation cho training run này.</Empty> : (
                <div className="space-y-2">
                  {visibleEvaluations.map((evaluation) => {
                    const successes = evaluation.episodes.filter((episode) => episode.success).length;
                    const checkpoint = selected.checkpoints?.find((item) => item.id === evaluation.checkpoint_id);
                    const expanded = expandedEvaluationId === evaluation.id;
                    return (
                      <div key={evaluation.id} className="rounded-lg border border-ink-700/60 bg-ink-850/50 p-3">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <div className="flex items-center gap-2">
                              <span className="text-sm font-medium">{checkpoint ? `Epoch ${checkpoint.epoch}` : evaluation.checkpoint_id}</span>
                              <Badge tone={TONES[evaluation.status]}>{evaluation.status}</Badge>
                            </div>
                            <div className="mt-1 text-xs text-ink-400">
                              {evaluation.task_name} · {evaluation.episodes.length}/{evaluation.num_episodes} rollouts · {timeAgo(evaluation.created_at)}
                            </div>
                          </div>
                          <div className="flex items-center gap-3">
                            {evaluation.success_rate != null && (
                              <div className="text-right">
                                <div className="text-lg font-semibold text-ok-400">{percent(evaluation.success_rate, 1)}</div>
                                <div className="text-[11px] text-ink-400">{successes}/{evaluation.episodes.length} successful</div>
                              </div>
                            )}
                            {(evaluation.status === "pending" || evaluation.status === "running") && (
                              <Button variant="danger" disabled={busy} onClick={() => void cancelEvaluation(evaluation.id)}>Cancel</Button>
                            )}
                            <Button variant="subtle" onClick={() => setExpandedEvaluationId(expanded ? null : evaluation.id)}>{expanded ? "Hide" : "View details"}</Button>
                          </div>
                        </div>
                        {expanded && (
                          <div className="mt-3 border-t border-ink-700/60 pt-3">
                            {evaluation.mean_episode_length != null && <div className="text-xs text-ink-400">Mean episode length: {evaluation.mean_episode_length.toFixed(1)} steps</div>}
                            {evaluation.episodes.length > 0 && <div className="mt-3 flex flex-wrap gap-1.5">{evaluation.episodes.map((episode) => <span key={episode.seed} title={`seed ${episode.seed} · ${episode.steps} steps`} className={`rounded px-2 py-1 text-[11px] ${episode.success ? "bg-ok-600/20 text-ok-400" : "bg-bad-600/20 text-bad-400"}`}>{episode.seed}: {episode.success ? "success" : "fail"}</span>)}</div>}
                            <EvaluationVideos evaluation={evaluation} />
                            {evaluation.error && <pre className="mt-3 whitespace-pre-wrap text-xs text-bad-400">{evaluation.error}</pre>}
                          </div>
                        )}
                      </div>
                    );
                  })}
                  {selectedEvaluations.length > 5 && <div className="pt-2 text-center"><Button variant="subtle" onClick={() => setShowAllEvaluations((value) => !value)}>{showAllEvaluations ? "Show recent only" : `View all evaluations (${selectedEvaluations.length})`}</Button></div>}
                </div>
              )}
              {latestEvaluation && evaluationLog && expandedEvaluationId === latestEvaluation.id && (
                <details className="mt-4">
                  <summary className="cursor-pointer text-xs text-ink-300">Latest evaluation log</summary>
                  <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded-lg border border-ink-700 bg-ink-950 p-3 text-[11px] text-ink-300">{evaluationLog}</pre>
                </details>
              )}
            </Card>

            <Card title="Job log" subtitle="Đóng mặc định; log vẫn được cập nhật trong khi RoboMimic đang chạy." actions={<Button variant="subtle" onClick={() => setShowJobLog((value) => !value)}>{showJobLog ? "Hide log" : "Open log"}</Button>}>
              {showJobLog && (log ? <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg border border-ink-700 bg-ink-950 p-3 text-[11px] leading-relaxed text-ink-300">{log}</pre> : <Empty>Chưa có log.</Empty>)}
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}

function NumberField({ label, value, min, onChange }: { label: string; value: number; min: number; onChange: (value: number) => void }) {
  return <Field label={label}><Input type="number" min={min} value={value} onChange={(event) => onChange(Number(event.target.value))} /></Field>;
}

function EvaluationVideos({ evaluation }: { evaluation: EvaluationRun }) {
  const recorded = evaluation.episodes.filter((episode) => episode.video);
  if (!recorded.length) return null;
  return (
    <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {recorded.map((episode) => (
        <div key={episode.seed}>
          <video
            controls
            muted
            loop
            preload="metadata"
            className="w-full rounded-lg border border-tech-border bg-tech-bg"
            src={mediaUrl(`/training/evaluations/${evaluation.id}/videos/${episode.video}`)}
          />
          <div className="mt-1 text-xs text-ink-400">
            seed {episode.seed} · {episode.steps} steps · {episode.success ? "success" : "fail"}
          </div>
        </div>
      ))}
    </div>
  );
}
