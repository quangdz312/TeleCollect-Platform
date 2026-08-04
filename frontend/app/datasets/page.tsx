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
import { api, type DatasetExport, type Summary, type Task } from "@/lib/api";
import { bytes, timeAgo } from "@/lib/format";

export default function DatasetsPage() {
  const { user } = useAuth();
  const [exports, setExports] = useState<DatasetExport[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [dvc, setDvc] = useState<{ available: boolean; reason?: string } | null>(null);

  const [name, setName] = useState("v1");
  const [format, setFormat] = useState("lerobot");
  const [taskFilter, setTaskFilter] = useState("");
  const [includeFailures, setIncludeFailures] = useState(false);
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [e, s, d] = await Promise.all([api.exports(), api.summary(), api.dvc()]);
    setExports(e);
    setSummary(s);
    setDvc(d);
  }, []);

  useEffect(() => {
    if (!user) return;
    void api.tasks().then(setTasks);
    void load();
  }, [user, load]);

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
      });
      setInfo(
        `Exported ${created.num_episodes} episodes / ${created.num_frames.toLocaleString()} frames ` +
          `(${bytes(created.size_bytes)})` +
          (created.dvc_hash ? ` · DVC ${created.dvc_hash.slice(0, 12)}` : ""),
      );
      await load();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Export failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">Datasets</h1>
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
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Name" hint="Becomes the directory and the DVC-tracked version">
              <Input value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Format">
              <Select value={format} onChange={(e) => setFormat(e.target.value)}>
                <option value="lerobot">LeRobot v2.1 (parquet + mp4)</option>
                <option value="rlds">RLDS (TFRecord / SequenceExample)</option>
              </Select>
            </Field>
            <Field label="Task filter" hint="Empty exports every task into one dataset">
              <Select value={taskFilter} onChange={(e) => setTaskFilter(e.target.value)}>
                <option value="">All tasks</option>
                {tasks.map((task) => (
                  <option key={task.id} value={task.id}>
                    {task.title}
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
            <Button variant="primary" disabled={busy} onClick={createExport}>
              {busy ? "Exporting…" : "Export dataset"}
            </Button>
            {dvc && (
              <Badge tone={dvc.available ? "ok" : "neutral"}>
                DVC {dvc.available ? "tracking enabled" : (dvc.reason ?? "unavailable")}
              </Badge>
            )}
          </div>
          <p className="mt-2 text-xs text-ink-400">
            Failures are excluded by default. They are still worth keeping: a labelled failure
            documents what went wrong, and some algorithms use them — but behaviour cloning
            should not imitate them.
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
                    <td className="py-2 text-right">{item.num_episodes}</td>
                    <td className="py-2 text-right">{item.num_frames.toLocaleString()}</td>
                    <td className="py-2 text-right">{bytes(item.size_bytes)}</td>
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
        <p className="text-sm text-ink-300">
          Datasets are tracked by DVC rather than committed to git: the payload lives in the DVC
          cache and remote, while a small hash pointer goes into git next to the code. The hash
          recorded on each export is also stored on every training run, so any policy can be
          traced back to the exact bytes it learned from.
        </p>
        <pre className="mt-3 overflow-x-auto rounded-lg border border-ink-700 bg-ink-950 p-3 text-xs text-ink-300">
{`dvc push                      # upload the dataset payload to the remote
git add -A && git commit -m "dataset v1"
dvc checkout                  # restore the exact dataset for this commit`}
        </pre>
      </Card>
    </div>
  );
}
