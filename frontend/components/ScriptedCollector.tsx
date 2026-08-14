"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, Badge, Button, Card, Field, Input, Select, Stat } from "@/components/ui";
import { labeling, type CollectionJob, type LabelingConfig } from "@/lib/labeling";

const POLL_MS = 1500;
const QUALITY = "clean";

export function ScriptedCollector() {
  const [config, setConfig] = useState<LabelingConfig | null>(null);
  const [job, setJob] = useState<CollectionJob | null>(null);
  const [task, setTask] = useState("lift");
  const [episodeCount, setEpisodeCount] = useState(5);
  const [seed, setSeed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadConfig = useCallback(async () => {
    const data = await labeling.config();
    setConfig(data);
    if (data.tasks.length && !data.tasks.some((item) => item.task === task)) setTask(data.tasks[0].task);
    return data;
  }, [task]);

  useEffect(() => {
    void loadConfig().catch((problem) => setError((problem as Error).message));
  }, [loadConfig]);

  useEffect(() => {
    const suggested = config?.suggested_seeds[`${task}:${QUALITY}`];
    if (suggested !== undefined) setSeed(suggested);
  }, [config, task]);

  const running = job?.status === "queued" || job?.status === "running";

  useEffect(() => {
    if (!running || !job) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await labeling.run(job.id);
        setJob(next);
        if (next.status === "succeeded") {
          setNotice(`Đã thu ${next.result.episodes ?? 0} episode. Dữ liệu sẵn sàng trong hàng đợi review scripted.`);
          await loadConfig();
        }
        if (next.status === "failed") setError(next.error ?? "Collection failed.");
      } catch (problem) {
        setError((problem as Error).message);
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [job, loadConfig, running]);

  const startRun = async () => {
    setError(null);
    setNotice(null);
    try {
      setJob(await labeling.startRun({ task, quality: QUALITY, episodes: episodeCount, seed }));
    } catch (problem) {
      setError((problem as Error).message);
    }
  };

  return (
    <div className="space-y-5">
      {error && <Alert>{error}</Alert>}
      {notice && !error && <Alert tone="ok">{notice}</Alert>}
      <div className="grid gap-3 md:grid-cols-5">
        <Stat label="Episodes" value={config?.workspace.episodes ?? 0} />
        <Stat label="Reviewed" value={config?.workspace.reviewed ?? 0} />
        <Stat label="Approved" value={config?.workspace.approved ?? 0} tone="ok" />
        <Stat label="Rejected" value={config?.workspace.rejected ?? 0} tone="bad" />
        <Stat label="Pending" value={config?.workspace.pending ?? 0} tone="warn" />
      </div>
      <Card title="Thu tự động" subtitle="Sinh episode scripted để chuyển sang hàng đợi review.">
        <div className="grid gap-3 md:grid-cols-[1fr_140px_140px_auto] md:items-end">
          <Field label="Task">
            <Select value={task} disabled={running} onChange={(event) => setTask(event.target.value)}>
              {config?.tasks.map((item) => (
                <option key={item.task} value={item.task}>{item.task} · {item.tool_label ?? item.tool}</option>
              ))}
            </Select>
          </Field>
          <Field label="Episodes">
            <Input type="number" min={1} max={200} value={episodeCount} disabled={running} onChange={(event) => setEpisodeCount(Number(event.target.value))} />
          </Field>
          <Field label="Seed">
            <Input type="number" min={0} value={seed} disabled={running} onChange={(event) => setSeed(Number(event.target.value))} />
          </Field>
          <Button variant="primary" disabled={running || !config} onClick={startRun}>
            {running ? `Đang chạy ${Math.round((job?.progress ?? 0) * 100)}%` : "Bắt đầu thu"}
          </Button>
        </div>
        <Alert tone="info">Auto-label accept/review/reject chưa bật; episode sẽ đi qua review thủ công trước.</Alert>
        {job && (
          <div className="mt-4 rounded-lg border border-ink-700/60 bg-ink-850/60 p-3 text-xs">
            <div className="mb-2 flex items-center justify-between">
              <Badge tone={job.status === "succeeded" ? "ok" : job.status === "failed" ? "bad" : "info"}>{job.status}</Badge>
              <span className="tabular text-ink-400">{job.done}/{job.total}</span>
            </div>
            <div className="max-h-36 space-y-1 overflow-y-auto text-ink-300">{job.log.slice(-8).map((line) => <div key={line}>{line}</div>)}</div>
          </div>
        )}
      </Card>
    </div>
  );
}
