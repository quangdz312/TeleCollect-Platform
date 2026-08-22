"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, Badge, Button, Card, Field, Input, Select, Stat } from "@/components/ui";
import { labeling, type CollectionJob, type LabelingConfig } from "@/lib/labeling";

const POLL_MS = 1500;

export function ScriptedCollector() {
  const [config, setConfig] = useState<LabelingConfig | null>(null);
  const [job, setJob] = useState<CollectionJob | null>(null);
  const [task, setTask] = useState("lift");
  const [quality, setQuality] = useState("clean");
  // Keep the raw text while editing so users can select the value, clear it,
  // and type e.g. "100". A number-controlled input turns the intermediate
  // empty value into 0 and makes direct replacement awkward in some browsers.
  const [episodeCount, setEpisodeCount] = useState("5");
  const [seed, setSeed] = useState(0);
  const [batchId, setBatchId] = useState("lift-scripted-v1.2");
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
    const suggested = config?.suggested_seeds[`${task}:${quality}`];
    if (suggested !== undefined) setSeed(suggested);
  }, [config, quality, task]);

  const running = job?.status === "queued" || job?.status === "running";

  useEffect(() => {
    if (!running || !job) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await labeling.run(job.id);
        setJob(next);
        if (next.status === "succeeded") {
          setNotice(`Collected ${next.result.episodes ?? 0} episodes. They are queued in the scripted review list.`);
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
    const parsedEpisodes = Number(episodeCount);
    if (!Number.isInteger(parsedEpisodes) || parsedEpisodes < 1 || parsedEpisodes > 200) {
      setError("Episodes must be a whole number between 1 and 200.");
      return;
    }
    try {
      setJob(await labeling.startRun({
        task, quality, episodes: parsedEpisodes, seed,
        collection_batch_id: batchId,
      }));
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
      <Card title="Scripted collection" subtitle="Generate scripted episodes and send them to the review queue.">
        <div className="grid gap-3 md:grid-cols-[1fr_180px_140px_140px_140px_auto] md:items-end">
          <Field label="Task">
            <Select value={task} disabled={running} onChange={(event) => setTask(event.target.value)}>
              {config?.tasks.map((item) => (
                <option key={item.task} value={item.task}>{item.task} · {item.tool_label ?? item.tool}</option>
              ))}
            </Select>
          </Field>
          <Field label="Collection batch" hint="Reuse one id across the clean/good/medium runs of a dataset">
            <Input
              value={batchId}
              disabled={running}
              onChange={(event) => setBatchId(event.target.value)}
            />
          </Field>
          <Field label="Quality">
            <Select value={quality} disabled={running} onChange={(event) => setQuality(event.target.value)}>
              {config?.qualities.map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </Select>
          </Field>
          <Field label="Episodes">
            <Input
              type="text"
              inputMode="numeric"
              pattern="[0-9]*"
              value={episodeCount}
              disabled={running}
              onFocus={(event) => event.currentTarget.select()}
              onChange={(event) => {
                const value = event.target.value;
                if (/^\d*$/.test(value)) setEpisodeCount(value);
              }}
              onBlur={() => {
                if (episodeCount === "") setEpisodeCount("5");
              }}
            />
          </Field>
          <Field label="Seed">
            <Input type="number" min={0} value={seed} disabled={running} onChange={(event) => setSeed(Number(event.target.value))} />
          </Field>
          <Button
            variant="primary"
            disabled={running || !config || !/^\d+$/.test(episodeCount) || !/^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$/.test(batchId)}
            onClick={startRun}
          >
            {running ? `Running ${Math.round((job?.progress ?? 0) * 100)}%` : "Start collection"}
          </Button>
        </div>
        <Alert tone="info">Noise decreases across phases; some non-clean episodes carry at most one controlled semantic fault. The auto-gate settles the clear-cut cases and leaves the uncertain ones for review.</Alert>
        {job && (
          <div className="mt-4 rounded-lg border border-ink-700/60 bg-ink-850/60 p-3 text-xs">
            <div className="mb-2 flex items-center justify-between">
              <Badge tone={job.status === "succeeded" ? "ok" : job.status === "failed" ? "bad" : "info"}>{job.status}</Badge>
              <span className="tabular text-ink-400">{job.done}/{job.total}</span>
            </div>
            <div className="max-h-36 space-y-1 overflow-y-auto text-ink-300">{job.log.slice(-8).map((line, i) => <div key={i}>{line}</div>)}</div>
          </div>
        )}
      </Card>
    </div>
  );
}
