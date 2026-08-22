"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import {
  Alert,
  Badge,
  Button,
  Card,
  Empty,
  Field,
  Input,
  Select,
} from "@/components/ui";
import { api, type DatasetExport, type Summary } from "@/lib/api";
import { labeling, type TaskOption } from "@/lib/labeling";
import { bytes, timeAgo } from "@/lib/format";

const EXPORT_POLL_INTERVAL_MS = 750;
const EXPORT_POLL_LIMIT = 160;

function delay(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

export default function DatasetsPage() {
  const { user } = useAuth();
  const [exports, setExports] = useState<DatasetExport[]>([]);
  const [tasks, setTasks] = useState<TaskOption[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [dvc, setDvc] = useState<{ available: boolean; reason?: string } | null>(null);

  const [name, setName] = useState("v1");
  const [format, setFormat] = useState("robomimic");
  const [taskFilter, setTaskFilter] = useState("");
  const [includeFailures, setIncludeFailures] = useState(false);
  const [overwrite, setOverwrite] = useState(false);
  const [batchId, setBatchId] = useState("lift-scripted-v1.2");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [e, s, d, scripted] = await Promise.all([
      api.exports(),
      api.summary(),
      api.dvc(),
      labeling.config().catch(() => null),
    ]);
    setExports(e);
    setTasks(scripted?.tasks ?? []);
    setSummary(scripted ? {
      ...s,
      approved_successes: s.approved_successes + scripted.workspace.approved_successes,
    } : s);
    setDvc(d);
  }, []);

  useEffect(() => {
    if (!user) return;
    void load();
  }, [user, load]);

  useEffect(() => {
    if (!user || !exports.some((item) => item.status === "building")) return;
    const timer = window.setInterval(() => { void load(); }, 1500);
    return () => window.clearInterval(timer);
  }, [exports, load, user]);

  if (!user) return null;
  const canExport = user.role === "reviewer" || user.role === "admin";

  async function createExport() {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      const created = await api.createExport({
        name,
        format,
        tasks: taskFilter ? [taskFilter] : [],
        include_failures: includeFailures,
        overwrite,
        collection_batch_id: batchId,
      });
      setInfo(`Building ${created.name}… The page will update when the HDF5 is ready.`);
      await load();
      let completed = created;
      for (let attempt = 0; attempt < EXPORT_POLL_LIMIT && completed.status === "building"; attempt += 1) {
        await delay(EXPORT_POLL_INTERVAL_MS);
        completed = await api.exportInfo(created.id);
        setExports((current) => current.map((item) => item.id === completed.id ? completed : item));
      }
      if (completed.status === "failed") {
        throw new Error(completed.error_message || `Export ${completed.name} failed.`);
      }
      if (completed.status !== "ready") {
        setInfo(`Export ${completed.name} is still building. Its status will continue updating below.`);
        return;
      }
      setInfo(
        `Exported ${completed.num_episodes} episodes / ${completed.num_frames.toLocaleString()} frames ` +
          `(${bytes(completed.size_bytes)})` +
          (completed.dvc_hash ? ` · DVC ${completed.dvc_hash.slice(0, 12)}` : ""),
      );
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Export failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="font-heading text-[22px] font-bold tracking-tight">Datasets</h1>
        <p className="mt-0.5 text-sm text-ink-400">
          An export is an immutable snapshot of the approved demonstrations, with each
          reviewer&apos;s trim applied and their decision recorded alongside every episode.
        </p>
      </div>

      {canExport && (
        <Card
          title="New export"
          subtitle={
            summary
              ? `${summary.approved_successes} approved successes are eligible right now`
              : undefined
          }
        >
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <Field label="Name" hint="Becomes the directory and the DVC-tracked version">
              <Input value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Collection batch" hint="Chỉ lấy episode thuộc batch này">
              <Input value={batchId} onChange={(e) => setBatchId(e.target.value)} />
            </Field>
            <Field label="Format">
              <Select value={format} onChange={(e) => setFormat(e.target.value)}>
                <option value="robomimic">RoboMimic (HDF5)</option>
              </Select>
            </Field>
            <Field label="Task" hint="RoboMimic BC requires one environment per dataset">
              <Select value={taskFilter} onChange={(e) => setTaskFilter(e.target.value)}>
                <option value="">Select a task…</option>
                {tasks.map((task) => (
                  <option key={task.task} value={task.task}>
                    {task.tool_label ?? task.task}
                  </option>
                ))}
              </Select>
            </Field>
            <div className="flex flex-col justify-end gap-2 text-xs">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={includeFailures}
                  onChange={(e) => setIncludeFailures(e.target.checked)}
                />
                Include approved failures
              </label>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={overwrite}
                  onChange={(e) => setOverwrite(e.target.checked)}
                />
                Overwrite if it exists
              </label>
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-3">
            <Button variant="primary" disabled={busy || !taskFilter || !batchId} onClick={createExport}>
              {busy ? "Exporting…" : "Export dataset"}
            </Button>
            {dvc && (
              <Badge tone={dvc.available ? "ok" : "neutral"}>
                DVC {dvc.available ? "tracking enabled" : (dvc.reason ?? "unavailable")}
              </Badge>
            )}
          </div>
          <p className="mt-2 text-xs text-ink-400">
            The exported HDF5 contains only human-approved scripted demonstrations and can be
            passed directly to RoboMimic BC. Export each task separately because every task has
            different environment metadata and observation semantics.
          </p>

          {error && (
            <div className="mt-3">
              <Alert>{error}</Alert>
            </div>
          )}
          {info && (
            <div className="mt-3">
              <Alert tone="ok">{info}</Alert>
            </div>
          )}
        </Card>
      )}

      <Card title="Exports">
        {exports.length === 0 ? (
          <Empty>No datasets exported yet.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wider text-ink-400">
                <tr>
                  <th className="pb-2">Name</th>
                  <th className="pb-2">Format</th>
                  <th className="pb-2">Tasks</th>
                  <th className="pb-2">Status</th>
                  <th className="pb-2 text-right">Episodes</th>
                  <th className="pb-2 text-right">Frames</th>
                  <th className="pb-2 text-right">Size</th>
                  <th className="pb-2">DVC hash</th>
                  <th className="pb-2">Created</th>
                  {user.role === "admin" && <th className="pb-2" />}
                </tr>
              </thead>
              <tbody className="tabular">
                {exports.map((item) => (
                  <tr key={item.id} className="border-t border-ink-700/50">
                    <td className="py-2 font-medium">{item.name}</td>
                    <td className="py-2">
                      <Badge tone="info">{item.format}</Badge>
                    </td>
                    <td className="py-2 text-xs text-ink-400">{item.tasks.join(", ")}</td>
                    <td className="py-2">
                      <Badge tone={item.status === "ready" ? "ok" : item.status === "failed" ? "bad" : "warn"}>
                        {item.status}
                      </Badge>
                    </td>
                    <td className="py-2 text-right">{item.status === "ready" ? item.num_episodes : "—"}</td>
                    <td className="py-2 text-right">{item.status === "ready" ? item.num_frames.toLocaleString() : "—"}</td>
                    <td className="py-2 text-right">{item.status === "ready" ? bytes(item.size_bytes) : "—"}</td>
                    <td className="py-2 font-mono text-xs text-ink-400">
                      {item.dvc_hash ? item.dvc_hash.slice(0, 16) : "—"}
                    </td>
                    <td className="py-2 text-xs text-ink-400">{timeAgo(item.created_at)}</td>
                    {user.role === "admin" && (
                      <td className="py-2 text-right">
                        <Button
                          variant="ghost"
                          onClick={async () => {
                            if (!confirm(`Delete export ${item.name}?`)) return;
                            await api.deleteExport(item.id);
                            await load();
                          }}
                        >
                          Delete
                        </Button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Version control">
        <p className="text-sm text-tech-text">
          Datasets are tracked by DVC rather than committed to git: the payload lives in the DVC
          cache and remote, while a small hash pointer goes into git next to the code. The hash
          recorded on each export is also stored on every training run, so any policy can be
          traced back to the exact bytes it learned from.
        </p>
        <pre className="mt-3 overflow-x-auto rounded-lg border border-tech-border bg-tech-bg p-3 font-mono text-xs text-tech-text">
{`dvc push                      # upload the dataset payload to the remote
git add -A && git commit -m "dataset v1"
dvc checkout                  # restore the exact dataset for this commit`}
        </pre>
      </Card>
    </div>
  );
}
