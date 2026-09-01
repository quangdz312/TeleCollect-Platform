"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLeftBar, useRailBack } from "@/components/AppShell";
import { useAuth } from "@/components/AuthProvider";
import { PageHeader } from "@/components/PageHeader";
import { AdvancedGroup, Alert, Badge, Button, Card, cx, Empty, Field, Input, Select } from "@/components/ui";
import {
  api,
  type DatasetExport,
  type RunStatus,
  type TrainingRequest,
  type TrainingRun,
} from "@/lib/api";
import { bytes, timeAgo } from "@/lib/format";

/** Ngoai component: object moi moi lan render se khien hook chay lai mai. */
const BACK_TO_SECTIONS = { label: "All sections", href: "/" };

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
  wandb_enabled: false,
  wandb_project: "telecollect-robot-learning",
  wandb_entity: null,
};

const RUN_PREFERENCES_KEY = "training-run-preferences-v1";

type RunPreferences = {
  archived: string[];
  pinned: string[];
};

function wandbProjectUrl(run: TrainingRun): string | null {
  const config = run.config;
  if (!config.wandb_enabled || typeof config.wandb_entity !== "string" || !config.wandb_entity) {
    return null;
  }
  const project = typeof config.wandb_project === "string" ? config.wandb_project : "";
  if (!project) return null;
  return `https://wandb.ai/${encodeURIComponent(config.wandb_entity)}/${encodeURIComponent(project)}`;
}

/** Truong cho thanh trai: nhan nho, khong chu thich -- cot chi rong 288px. */
function RailField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] font-medium text-ink-300">{label}</span>
      {children}
    </label>
  );
}

function RailNumber({
  label,
  value,
  min,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  onChange: (value: number) => void;
}) {
  return (
    <RailField label={label}>
      <Input
        type="number"
        className="px-2 py-1 text-xs"
        min={min}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </RailField>
  );
}

export default function TrainingPage() {
  const { user } = useAuth();
  const router = useRouter();
  const [datasets, setDatasets] = useState<DatasetExport[]>([]);
  /** `null` until known — the warning must not flash before the check lands. */
  const [wandbConfigured, setWandbConfigured] = useState<boolean | null>(null);
  const [runs, setRuns] = useState<TrainingRun[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<TrainingRequest>(INITIAL_FORM);
  const [log, setLog] = useState("");
  const [selectedTask, setSelectedTask] = useState("all");
  const [runView, setRunView] = useState<"active" | "archived" | "all">("active");
  const [preferences, setPreferences] = useState<RunPreferences>({ archived: [], pinned: [] });
  const [showAllCheckpoints, setShowAllCheckpoints] = useState(false);
  const [showJobLog, setShowJobLog] = useState(false);
  const [showNewRun, setShowNewRun] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestedDatasetApplied = useRef(false);
  const requestedRunApplied = useRef(false);

  const canTrain = user?.role === "reviewer" || user?.role === "admin";
  // CPU staging sets this build-time flag to "false" (no GPU, no training
  // dependencies in the production image yet); default stays enabled so the
  // dev/demo build is unaffected. Checking it here avoids calling the
  // endpoint just to have it fail with a 403.
  const trainingEnabled = process.env.NEXT_PUBLIC_TRAINING_ENABLED !== "false";
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
  const selectedWandbUrl = selected ? wandbProjectUrl(selected) : null;

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
    // A transient backend restart may make the first load fail. Once both
    // requests succeed, clear that stale connectivity error; otherwise the
    // page keeps showing "Cannot reach backend" even though its data and form
    // have already loaded successfully.
    setError(null);
    // `?run=` đến từ trang Evaluate: mở đúng lần train đang được đánh giá.
    // Chỉ áp một lần, sau đó lựa chọn là của người dùng.
    const requestedRunId = new URLSearchParams(window.location.search).get("run");
    if (requestedRunId && !requestedRunApplied.current && allRuns.some((run) => run.id === requestedRunId)) {
      requestedRunApplied.current = true;
      setSelectedId(requestedRunId);
    } else {
      setSelectedId((current) => current ?? allRuns[0]?.id ?? null);
    }
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
    // Only the flag, never the key — the endpoint does not return one.
    void api
      .wandbSettings()
      .then((settings) => setWandbConfigured(settings.configured))
      .catch(() => setWandbConfigured(null));
  }, [user]);

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

  /**
   * Thanh trái: nút mở cấu hình ở trên, danh sách các lần train ở dưới.
   *
   * Cùng khuôn với trang Evaluate — hai trang này làm hai việc song song nên
   * để chúng khác bố cục là bắt người dùng học lại từ đầu ở trang thứ hai.
   */
  const trainingRail = useMemo(
    () => (
      <div className="flex min-h-0 flex-col gap-3">
        {canTrain && (
          <Button
            variant="primary"
            className="w-full justify-center"
            onClick={() => setShowNewRun((value) => !value)}
          >
            {showNewRun ? "Close setup" : "+ New run"}
          </Button>
        )}

        {/* Cau hinh nam ngay trong thanh, khong phai mot the giua trang: no la
            thu nguoi dung dung vao moi lan chay, nen de no dung yen mot cho.
            Mot cot doc chu khong phai luoi bon cot -- cot nay chi rong 240px.
            Nhung tham so hiem khi doi gap vao Advanced de phan tren con doc duoc. */}
        {canTrain && showNewRun && (
          datasets.length === 0 ? (
            <p className="px-1 text-xs text-ink-400">
              Export a RoboMimic dataset in the ready state first.
            </p>
          ) : (
            <div className="space-y-2.5 border-b border-ink-700 pb-3">
              <RailField label="Run name">
                <Input className="px-2 py-1 text-xs" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
              </RailField>
              <RailField label="Dataset">
                <Select className="px-2 py-1 text-xs" value={form.dataset_id} onChange={(event) => setForm({ ...form, dataset_id: event.target.value })}>
                  {datasets.map((dataset) => (
                    <option key={dataset.id} value={dataset.id}>{dataset.name} · {dataset.num_episodes} ep</option>
                  ))}
                </Select>
              </RailField>
              <RailField label="Policy">
                <Select className="px-2 py-1 text-xs" value={form.policy} onChange={(event) => setForm({ ...form, policy: event.target.value as "bc" | "bc-rnn" })}>
                  <option value="bc">BC</option>
                  <option value="bc-rnn">BC-RNN (LSTM)</option>
                </Select>
              </RailField>
              <div className="grid grid-cols-2 gap-2">
                <RailNumber label="Epochs" value={form.epochs} min={1} onChange={(epochs) => setForm({ ...form, epochs })} />
                <RailNumber label="Batch size" value={form.batch_size} min={1} onChange={(batch_size) => setForm({ ...form, batch_size })} />
                <RailNumber label="Save every" value={form.save_every_n_epochs ?? 1} min={1} onChange={(save_every_n_epochs) => setForm({ ...form, save_every_n_epochs })} />
                <RailField label="Learning rate">
                  <Input type="number" className="px-2 py-1 text-xs" min="0.0000001" step="0.00001" value={form.learning_rate} onChange={(event) => setForm({ ...form, learning_rate: Number(event.target.value) })} />
                </RailField>
              </div>

              <AdvancedGroup title="Advanced">
                <div className="w-full space-y-2.5">
                  <RailField label="Device">
                    <Select className="px-2 py-1 text-xs" value={form.device} onChange={(event) => setForm({ ...form, device: event.target.value as TrainingRequest["device"] })}>
                      <option value="auto">Auto</option>
                      <option value="cuda">CUDA GPU</option>
                      <option value="cpu">CPU</option>
                    </Select>
                  </RailField>
                  <div className="grid grid-cols-2 gap-2">
                    <RailNumber label="Workers" value={form.num_workers} min={0} onChange={(num_workers) => setForm({ ...form, num_workers })} />
                    <RailNumber label="Seed" value={form.seed} min={0} onChange={(seed) => setForm({ ...form, seed })} />
                  </div>
                  {form.policy === "bc-rnn" && (
                    <div className="grid grid-cols-2 gap-2">
                      <RailNumber label="Seq length" value={form.sequence_length} min={1} onChange={(sequence_length) => setForm({ ...form, sequence_length })} />
                      <RailNumber label="RNN hidden" value={form.rnn_hidden_dim} min={1} onChange={(rnn_hidden_dim) => setForm({ ...form, rnn_hidden_dim })} />
                      <RailNumber label="RNN layers" value={form.rnn_layers} min={1} onChange={(rnn_layers) => setForm({ ...form, rnn_layers })} />
                    </div>
                  )}
                  <RailField label="Observations">
                    <Select className="px-2 py-1 text-xs" value={form.observation_profile} onChange={(event) => setForm({ ...form, observation_profile: event.target.value as TrainingRequest["observation_profile"] })}>
                      <option value="minimal">Minimal task state</option>
                      <option value="all">All observations</option>
                    </Select>
                  </RailField>
                  <RailField label="Normalize">
                    <Select className="px-2 py-1 text-xs" value={form.normalize_observations ? "yes" : "no"} onChange={(event) => setForm({ ...form, normalize_observations: event.target.value === "yes" })}>
                      <option value="yes">Enabled</option>
                      <option value="no">Disabled</option>
                    </Select>
                  </RailField>
                  <RailField label="Training rollouts">
                    <Select className="px-2 py-1 text-xs" value={form.rollout_enabled ? "yes" : "no"} onChange={(event) => setForm({ ...form, rollout_enabled: event.target.value === "yes" })}>
                      <option value="yes">Enabled</option>
                      <option value="no">Disabled</option>
                    </Select>
                  </RailField>
                  {form.rollout_enabled && (
                    <div className="grid grid-cols-2 gap-2">
                      <RailNumber label="Every N" value={form.rollout_every_n_epochs} min={1} onChange={(rollout_every_n_epochs) => setForm({ ...form, rollout_every_n_epochs })} />
                      <RailNumber label="Per check" value={form.rollout_episodes} min={1} onChange={(rollout_episodes) => setForm({ ...form, rollout_episodes })} />
                      <RailNumber label="Horizon" value={form.rollout_horizon} min={1} onChange={(rollout_horizon) => setForm({ ...form, rollout_horizon })} />
                    </div>
                  )}
                  <RailField label="Weights &amp; Biases">
                    <Select className="px-2 py-1 text-xs" value={form.wandb_enabled ? "yes" : "no"} onChange={(event) => setForm({ ...form, wandb_enabled: event.target.value === "yes" })}>
                      <option value="no">Disabled</option>
                      <option value="yes">Track this run</option>
                    </Select>
                  </RailField>
                  {form.wandb_enabled && (
                    <>
                      <RailField label="W&amp;B project">
                        <Input className="px-2 py-1 text-xs" value={form.wandb_project} onChange={(event) => setForm({ ...form, wandb_project: event.target.value })} />
                      </RailField>
                      <RailField label="W&amp;B entity">
                        <Input className="px-2 py-1 text-xs" value={form.wandb_entity ?? ""} onChange={(event) => setForm({ ...form, wandb_entity: event.target.value || null })} />
                      </RailField>
                    </>
                  )}
                </div>
              </AdvancedGroup>

              {form.wandb_enabled && wandbConfigured === false && (
                <p className="text-[11px] text-bad-400">
                  No W&amp;B account connected — add a key in Settings or disable tracking.
                </p>
              )}

              <Button
                variant="primary"
                className="w-full justify-center"
                disabled={busy || !trainingEnabled || !form.dataset_id || !form.name.trim()}
                onClick={() => void startTraining()}
              >
                {busy ? "Starting…" : "Start training"}
              </Button>
            </div>
          )
        )}

        <div className="grid grid-cols-2 gap-2">
          <Field label="Task">
            <Select
              className="px-2 py-1 text-xs"
              value={selectedTask}
              onChange={(event) => selectTask(event.target.value)}
            >
              <option value="all">All tasks</option>
              {taskNames.map((task) => <option key={task} value={task}>{task}</option>)}
            </Select>
          </Field>
          <Field label="Show">
            <Select
              className="px-2 py-1 text-xs"
              value={runView}
              onChange={(event) => setRunView(event.target.value as typeof runView)}
            >
              <option value="active">Active</option>
              <option value="archived">Archived</option>
              <option value="all">All</option>
            </Select>
          </Field>
        </div>

        <div className="min-h-0 flex-1 border-t border-ink-700 pt-3">
          <p className="px-1 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-ink-400">
            Training runs
          </p>
          {visibleRuns.length === 0 ? (
            <p className="px-1 text-xs text-ink-400">No run matches the filter.</p>
          ) : (
            <ul className="space-y-1">
              {visibleRuns.map((run) => (
                <li key={run.id}>
                  <button
                    onClick={() => setSelectedId(run.id)}
                    className={cx(
                      "w-full rounded-lg border px-2.5 py-2 text-left transition-colors",
                      selectedId === run.id
                        ? "border-accent-500/60 bg-ink-850"
                        : "border-transparent hover:bg-ink-850",
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium">
                        {preferences.pinned.includes(run.id) ? "★ " : ""}{run.name}
                      </span>
                      <Badge tone={TONES[run.status]}>{run.status}</Badge>
                    </div>
                    <div className="mt-0.5 truncate text-[11px] text-ink-400">
                      {taskForRun(run)} · {String(run.config.policy).toUpperCase()} · {timeAgo(run.created_at)}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    ),
    // `form` và `datasets` phải nằm đây: cả biểu mẫu nằm trong thanh này, nên
    // thiếu chúng thì thanh giữ nguyên bản vẽ cũ và mọi ô trở nên gõ không được
    // — `setForm` đổi state nhưng không có gì vẽ lại.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      visibleRuns, selectedId, selectedTask, runView, taskNames, preferences,
      canTrain, showNewRun, form, datasets, busy, trainingEnabled, wandbConfigured,
    ],
  );

  // Trên early return: hook phải chạy ở mọi lần render.
  useLeftBar(trainingRail);
  useRailBack(BACK_TO_SECTIONS);

  if (!user) return null;

  async function startTraining() {
    if (!trainingEnabled) {
      setError("Training chưa khả dụng trong bản CPU staging.");
      return;
    }
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
    <div className="space-y-3">
      <PageHeader
        eyebrow="Imitation learning lab"
        title="Training"
        description="Train and manage robot policies with real-world demonstrations."
      />

      {error && <Alert>{error}</Alert>}
      {canTrain && !trainingEnabled && (
        <Alert tone="info">Training chưa khả dụng trong bản CPU staging. Bản demo này chưa chạy trên GPU và chưa cài dependency huấn luyện.</Alert>
      )}


      <Card className="shadow-sm">
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

      {/* Danh sách run đã chuyển sang thanh trái, nên phần chi tiết chiếm trọn
          chiều ngang thay vì nhường một cột cho thứ đã có chỗ khác. */}
      <div>
        {!selected ? <Empty>Select a training run to see its details.</Empty> : (
          <div className="space-y-3">
            <Card
              title={<span className="flex items-center gap-2"><TrainingArmIcon /><span>{selected.name}</span></span>}
              subtitle={`${taskForRun(selected)} · ${String(selected.config.policy).toUpperCase()} · ${totalEpochs} epochs`}
              actions={<div className="flex flex-wrap gap-2"><Badge tone={TONES[selected.status]}>{selected.status}</Badge>{selectedWandbUrl && <Button variant="subtle" onClick={() => window.open(selectedWandbUrl, "_blank", "noopener,noreferrer")}><ActionIcon kind="external" />Open W&B</Button>}<Button variant="subtle" onClick={() => togglePinned(selected.id)}><ActionIcon kind="pin" />{preferences.pinned.includes(selected.id) ? "Unpin" : "Pin"}</Button><Button variant="subtle" onClick={() => toggleArchived(selected.id)}><ActionIcon kind="archive" />{preferences.archived.includes(selected.id) ? "Restore" : "Archive"}</Button>{canTrain && (selected.status === "running" || selected.status === "pending") && <Button variant="danger" disabled={busy} onClick={() => void cancelTraining()}><ActionIcon kind="cancel" />Cancel</Button>}{user.role === "admin" && selected.status !== "running" && selected.status !== "pending" && <Button variant="danger" disabled={busy} onClick={() => void deleteTraining()}><ActionIcon kind="cancel" />Delete</Button>}</div>}
            >
              <div className="grid gap-2 sm:grid-cols-3">
                <TrainingMetric label="Epoch" value={`${currentEpoch} / ${totalEpochs}`} />
                <TrainingMetric label="Train loss" value={selected.train_loss == null ? "—" : selected.train_loss.toFixed(6)} />
                <TrainingMetric label="Validation loss" value={selected.validation_loss == null ? "—" : selected.validation_loss.toFixed(6)} />
              </div>
              <div className="mt-4 h-2 overflow-hidden rounded-full bg-ink-800"><div className="h-full bg-accent-500 transition-all" style={{ width: `${progress}%` }} /></div>
              <div className="mt-1 text-right text-xs tabular text-ink-400">{progress.toFixed(1)}%</div>
              <TrainingCurve checkpoints={selected.checkpoints ?? []} trainLoss={selected.train_loss} validationLoss={selected.validation_loss} />
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

function TrainingMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-ink-700 bg-ink-900 px-3 py-3 shadow-[0_1px_2px_rgba(15,23,42,.03)]">
      <p className="text-[10px] font-semibold text-ink-400">{label}</p>
      <p className="mt-1.5 font-heading text-xl font-bold tabular-nums text-ink-100">{value}</p>
    </div>
  );
}

function TrainingArmIcon() {
  return (
    <svg className="h-8 w-8 shrink-0 text-accent-500" viewBox="0 0 36 36" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 30h14M7 30l2-7h7l2 7M10 23v-8l5-5 5 3 4 8" />
      <circle cx="10" cy="15" r="2.5" /><circle cx="15" cy="10" r="2.5" /><circle cx="20" cy="13" r="2.5" /><circle cx="24" cy="21" r="2.5" />
      <path d="m26 20 4-2 2 4-4 2M27 24l2 4M31 22l2 4" />
    </svg>
  );
}

function FunnelIcon() {
  return <svg className="h-4 w-4 text-ink-400" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 4h14l-5.5 6v4.5l-3 1.5v-6L3 4Z" /></svg>;
}

function ActionIcon({ kind }: { kind: "external" | "pin" | "archive" | "cancel" }) {
  const path = kind === "external"
    ? <><path d="M11 4h5v5M16 4l-7 7"/><path d="M14 11v5H4V6h5"/></>
    : kind === "pin"
      ? <><path d="m7 3 6 6M6 8l6 6M9 5l4-2 2 2-2 4M6 11l-3 6 6-3"/></>
      : kind === "archive"
        ? <><rect x="3" y="5" width="14" height="12" rx="2"/><path d="M2 5h16V2H2v3M8 9h4"/></>
        : <><circle cx="10" cy="10" r="7"/><path d="m8 8 4 4M12 8l-4 4"/></>;
  return <svg className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{path}</svg>;
}

function TrainingCurve({ checkpoints, trainLoss, validationLoss }: { checkpoints: NonNullable<TrainingRun["checkpoints"]>; trainLoss?: number | null; validationLoss?: number | null }) {
  const values = checkpoints
    .filter((item) => item.validation_loss != null)
    .map((item) => ({ epoch: item.epoch, value: item.validation_loss as number }))
    .sort((a, b) => a.epoch - b.epoch);
  if (values.length < 2) return null;
  const width = 900;
  const height = 150;
  const maxEpoch = Math.max(...values.map((item) => item.epoch), 1);
  const maxLoss = Math.max(...values.map((item) => item.value), validationLoss ?? 0, trainLoss ?? 0, 0.000001);
  const points = values.map((item) => `${(item.epoch / maxEpoch) * width},${height - (item.value / maxLoss) * (height - 18)}`).join(" ");
  return (
    <div className="mt-4 rounded-xl border border-ink-700/80 bg-ink-850/55 p-3">
      <div className="mb-2 flex items-center justify-between gap-3 text-[11px]"><span className="font-semibold uppercase tracking-wider text-ink-400">Validation loss history</span><span className="flex items-center gap-1.5 text-accent-500"><i className="h-0.5 w-5 bg-accent-500" /> checkpoint loss</span></div>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-36 w-full overflow-visible" preserveAspectRatio="none" role="img" aria-label="Validation loss across checkpoints">
        {[0.25, 0.5, 0.75].map((ratio) => <line key={ratio} x1="0" x2={width} y1={height * ratio} y2={height * ratio} stroke="rgb(203 213 225 / .7)" strokeDasharray="4 5" />)}
        <polyline points={points} fill="none" stroke="#2563eb" strokeWidth="3" vectorEffect="non-scaling-stroke" />
        {values.map((item) => <circle key={item.epoch} cx={(item.epoch / maxEpoch) * width} cy={height - (item.value / maxLoss) * (height - 18)} r="4" fill="#fff" stroke="#2563eb" strokeWidth="2" vectorEffect="non-scaling-stroke" />)}
      </svg>
      <div className="flex justify-between text-[10px] tabular text-ink-400"><span>Epoch {values[0].epoch}</span><span>Epoch {maxEpoch}</span></div>
    </div>
  );
}
