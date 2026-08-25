"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Alert, Badge, Button, Card, Empty, Field, Input, Select, Stat } from "@/components/ui";
import {
  api,
  type DatasetExport,
  type RunStatus,
  type TrainingRequest,
  type TrainingRun,
} from "@/lib/api";
import { bytes, timeAgo } from "@/lib/format";

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
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<TrainingRequest>(INITIAL_FORM);
  const [log, setLog] = useState("");
  const [selectedTask, setSelectedTask] = useState("all");
  const [runView, setRunView] = useState<"active" | "archived" | "all">("active");
  const [preferences, setPreferences] = useState<RunPreferences>({ archived: [], pinned: [] });
  const [showAllCheckpoints, setShowAllCheckpoints] = useState(false);
  const [showJobLog, setShowJobLog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestedDatasetApplied = useRef(false);

  const canTrain = user?.role === "reviewer" || user?.role === "admin";
  const datasetById = useMemo(
    () => new Map(datasets.map((dataset) => [dataset.id, dataset])),
    [datasets],
  );
  const selectedTrainingDataset = datasetById.get(form.dataset_id);
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
  const compactCheckpoints = useMemo(() => {
    const checkpoints = selected?.checkpoints ?? [];
    if (showAllCheckpoints) return checkpoints;
    const important = checkpoints.filter(
      (checkpoint) => checkpoint.is_best_validation || checkpoint.is_latest,
    );
    return Array.from(new Map(important.map((checkpoint) => [checkpoint.id, checkpoint])).values());
  }, [selected?.checkpoints, showAllCheckpoints]);

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
    const [allDatasets, allRuns] = await Promise.all([
      api.exports(),
      api.runs(),
    ]);
    const ready = allDatasets.filter(
      (item) => item.format === "robomimic" && item.status === "ready",
    );
    setDatasets(ready);
    setRuns(allRuns);
    setSelectedId((current) => current ?? allRuns[0]?.id ?? null);
    const requestedDatasetId = new URLSearchParams(window.location.search).get("dataset");
    if (requestedDatasetId && !requestedDatasetApplied.current) {
      requestedDatasetApplied.current = true;
      const requestedDataset = ready.find((dataset) => dataset.id === requestedDatasetId);
      if (requestedDataset) {
        setForm((current) => ({
          ...current,
          dataset_id: requestedDataset.id,
          name: current.name === INITIAL_FORM.name ? `${requestedDataset.name}-bc-v1` : current.name,
        }));
      } else {
        setError("The requested dataset is unavailable, not ready, or is not a RoboMimic HDF5 dataset.");
      }
    } else {
      setForm((current) =>
        current.dataset_id || !ready.length ? current : { ...current, dataset_id: ready[0].id },
      );
    }
  }, []);

  useEffect(() => {
    if (!user) return;
    void load().catch((exc) =>
      setError(exc instanceof Error ? exc.message : "Could not load training data"),
    );
  }, [load, user]);

  useEffect(() => {
    const active = runs.some((run) => run.status === "pending" || run.status === "running");
    if (!active) return;
    const timer = window.setInterval(() => void load(), 2500);
    return () => window.clearInterval(timer);
  }, [load, runs]);

  useEffect(() => {
    setShowAllCheckpoints(false);
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
      setError(exc instanceof Error ? exc.message : "Could not start training");
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
      setError(exc instanceof Error ? exc.message : "Could not cancel training");
    } finally {
      setBusy(false);
    }
  }

  async function deleteTraining() {
    if (!selected || user?.role !== "admin") return;
    setBusy(true);
    setError(null);
    try {
      const relatedEvaluations = await api.evaluations(selected.id);
      const checkpointCount = selected.checkpoints?.length ?? 0;
      const evaluationText = relatedEvaluations.length === 1
        ? "1 evaluation and its videos"
        : `${relatedEvaluations.length} evaluations and their videos`;
      const confirmed = window.confirm(
        `Delete training run "${selected.name}"?\n\n` +
        `This permanently deletes ${checkpointCount} checkpoints, the training log, and ${evaluationText}. ` +
        "The source dataset will not be deleted.",
      );
      if (!confirmed) return;
      await api.deleteRun(selected.id);
      const deletedId = selected.id;
      const remaining = runs.filter((run) => run.id !== deletedId);
      setRuns(remaining);
      setSelectedId(remaining[0]?.id ?? null);
      updatePreferences({
        archived: preferences.archived.filter((id) => id !== deletedId),
        pinned: preferences.pinned.filter((id) => id !== deletedId),
      });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not delete training run");
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
          Train BC or BC-RNN from an approved HDF5 dataset and follow the checkpoints.
        </p>
      </div>

      {error && <Alert>{error}</Alert>}

      {canTrain && (
        <Card title="New training run" subtitle="The backend runs one job at a time so runs do not contend for the GPU.">
          {datasets.length === 0 ? (
            <Empty>Export at least one RoboMimic dataset in the ready state first.</Empty>
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
              {selectedTrainingDataset && (
                <div className="mt-3">
                  <Alert tone="info">
                    Training from <strong>{selectedTrainingDataset.name}</strong> · {selectedTrainingDataset.num_episodes.toLocaleString()} episodes · {selectedTrainingDataset.num_frames.toLocaleString()} frames · {selectedTrainingDataset.tasks.join(", ") || "unknown task"}
                  </Alert>
                </div>
              )}
              {form.normalize_observations && (
                <div className="mt-3"><Alert tone="info">RoboMimic does not support normalization together with a validation split. This run picks its checkpoint by simulator rollout success instead of validation loss.</Alert></div>
              )}
              <div className="mt-4 flex items-center gap-3">
                <Button variant="primary" disabled={busy || !form.dataset_id || !form.name.trim()} onClick={() => void startTraining()}>
                  {busy ? "Starting…" : "Start training"}
                </Button>
                <span className="text-xs text-ink-400">Training keeps running on the backend if you switch tabs.</span>
              </div>
            </>
          )}
        </Card>
      )}

      <Card title="Training workspace" subtitle="Pick a task and a run; archived runs can be reopened at any time.">
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
          {visibleRuns.length === 0 ? <Empty>No training run matches the filter.</Empty> : (
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

        {!selected ? <Empty>Select a training run to see its details.</Empty> : (
          <div className="space-y-5">
            <Card title={selected.name} subtitle={`${taskForRun(selected)} · ${String(selected.config.policy).toUpperCase()} · ${totalEpochs} epochs`} actions={<div className="flex flex-wrap gap-2"><Badge tone={TONES[selected.status]}>{selected.status}</Badge><Button variant="subtle" onClick={() => togglePinned(selected.id)}>{preferences.pinned.includes(selected.id) ? "Unpin" : "Pin"}</Button><Button variant="subtle" onClick={() => toggleArchived(selected.id)}>{preferences.archived.includes(selected.id) ? "Restore" : "Archive"}</Button>{canTrain && (selected.status === "running" || selected.status === "pending") && <Button variant="danger" disabled={busy} onClick={() => void cancelTraining()}>Cancel</Button>}{user.role === "admin" && selected.status !== "running" && selected.status !== "pending" && <Button variant="danger" disabled={busy} onClick={() => void deleteTraining()}>Delete</Button>}</div>}>
              <div className="grid gap-3 sm:grid-cols-3">
                <Stat label="Epoch" value={`${currentEpoch} / ${totalEpochs}`} />
                <Stat label="Train loss" value={selected.train_loss == null ? "—" : selected.train_loss.toFixed(6)} />
                <Stat label="Validation loss" value={selected.validation_loss == null ? "—" : selected.validation_loss.toFixed(6)} />
              </div>
              <div className="mt-4 h-2 overflow-hidden rounded-full bg-ink-800"><div className="h-full bg-accent-500 transition-all" style={{ width: `${progress}%` }} /></div>
              <div className="mt-1 text-right text-xs tabular text-ink-400">{progress.toFixed(1)}%</div>
              {selected.error && <div className="mt-3"><Alert><pre className="whitespace-pre-wrap text-xs">{selected.error}</pre></Alert></div>}
            </Card>

            <Card title="Checkpoints" subtitle="Best validation is the checkpoint with the lowest validation loss.">
              {!selected.checkpoints?.length ? <Empty>No checkpoint yet.</Empty> : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[900px] text-left text-sm">
                    <thead className="text-xs uppercase text-ink-400"><tr><th className="px-2 py-2">Epoch</th><th className="px-2 py-2">Validation loss</th><th className="px-2 py-2">Size</th><th className="px-2 py-2">Created</th><th className="px-2 py-2">File</th><th className="px-2 py-2">Tags</th><th className="px-2 py-2 text-right">Actions</th></tr></thead>
                    <tbody className="divide-y divide-ink-700/60">
                      {compactCheckpoints.map((checkpoint) => (
                        <tr key={checkpoint.id}>
                          <td className="px-2 py-2 tabular">{checkpoint.epoch}</td>
                          <td className="px-2 py-2 tabular">{checkpoint.validation_loss == null ? "—" : checkpoint.validation_loss.toFixed(8)}</td>
                          <td className="px-2 py-2">{bytes(checkpoint.size_bytes)}</td>
                          <td className="px-2 py-2 text-xs text-ink-400">{timeAgo(checkpoint.created_at)}</td>
                          <td className="max-w-72 truncate px-2 py-2 font-mono text-xs text-ink-300" title={checkpoint.filename}>{checkpoint.filename}</td>
                          <td className="px-2 py-2"><div className="flex gap-1">{checkpoint.is_best_validation && <Badge tone="ok">best validation</Badge>}{checkpoint.is_latest && <Badge tone="info">latest</Badge>}</div></td>
                          <td className="px-2 py-2"><div className="flex justify-end gap-2">
                            <a href={api.checkpointDownloadUrl(selected.id, checkpoint.id)}><Button variant="subtle">Download</Button></a>
                            {selected.status === "succeeded" && <a href={`/evaluate?run=${encodeURIComponent(selected.id)}&checkpoint=${encodeURIComponent(checkpoint.id)}`}><Button variant="primary">Evaluate</Button></a>}
                          </div></td>
                        </tr>
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

            <Card title="Job log" subtitle="Collapsed by default; the log keeps updating while RoboMimic runs." actions={<Button variant="subtle" onClick={() => setShowJobLog((value) => !value)}>{showJobLog ? "Hide log" : "Open log"}</Button>}>
              {showJobLog && (log ? <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg border border-ink-700 bg-ink-950 p-3 text-[11px] leading-relaxed text-ink-300">{log}</pre> : <Empty>No log yet.</Empty>)}
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
