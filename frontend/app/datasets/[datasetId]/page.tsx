"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Alert, Badge, Button, Card, Empty, Stat } from "@/components/ui";
import { api, type DatasetDetail } from "@/lib/api";
import { bytes, timeAgo } from "@/lib/format";

function statusTone(status: string): "ok" | "bad" | "warn" | "neutral" {
  if (status === "ready") return "ok";
  if (status === "failed") return "bad";
  if (status === "building") return "warn";
  return "neutral";
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="flex justify-between gap-4 border-b border-ink-700/60 py-2.5 last:border-0"><dt className="text-xs text-ink-400">{label}</dt><dd className="break-all text-right text-sm">{value}</dd></div>;
}

export default function DatasetDetailPage() {
  const { user } = useAuth();
  const params = useParams<{ datasetId: string }>();
  const datasetId = decodeURIComponent(params.datasetId);
  const [dataset, setDataset] = useState<DatasetDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try { setDataset(await api.datasetDetail(datasetId)); setError(null); }
    catch (problem) { setError(problem instanceof Error ? problem.message : "Could not load dataset"); }
  }, [datasetId]);

  useEffect(() => { if (user) void load(); }, [load, user]);
  useEffect(() => {
    if (!dataset || dataset.status !== "building") return;
    const timer = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(timer);
  }, [dataset, load]);

  if (!user) return null;
  if (error && !dataset) return <Alert>{error}</Alert>;
  if (!dataset) return <Empty>Loading dataset…</Empty>;
  const fields = dataset.schema_manifest.fields ?? [];

  return <div className="space-y-5">
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div><Link href="/datasets" className="text-xs text-accent-500 hover:underline">← Datasets</Link><h1 className="mt-1 font-heading text-[22px] font-bold">{dataset.name}</h1><p className="font-mono text-xs text-ink-400">{dataset.id}</p></div>
      <div className="flex flex-wrap gap-2">
        <Badge tone={statusTone(dataset.status)}>{dataset.status}</Badge>
        {dataset.status === "ready" && dataset.format === "robomimic" && (
          <Link href={`/training?dataset=${encodeURIComponent(dataset.id)}`}>
            <Button variant="primary">Train this dataset</Button>
          </Link>
        )}
        {dataset.status === "ready" && <a href={api.datasetDownloadUrl(dataset.id)}><Button variant="subtle">{dataset.format === "robomimic" ? "Download HDF5" : dataset.format === "lerobot" ? "Download LeRobot (ZIP)" : "Download ZIP"}</Button></a>}
        {dataset.status === "failed" && <Button disabled={busy} onClick={async () => { setBusy(true); try { await api.retryExport(dataset.id); await load(); } catch (problem) { setError(problem instanceof Error ? problem.message : "Retry failed"); } finally { setBusy(false); } }}>Retry build</Button>}
      </div>
    </div>
    {error && <Alert>{error}</Alert>}
    {dataset.error_message && <Alert><strong>Build failed:</strong> {dataset.error_message}</Alert>}

    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><Stat label="Episodes" value={dataset.num_episodes.toLocaleString()} /><Stat label="Frames" value={dataset.num_frames.toLocaleString()} /><Stat label="Size" value={bytes(dataset.size_bytes)} /><Stat label="Created" value={timeAgo(dataset.created_at)} /></div>

    <div className="grid gap-5 lg:grid-cols-2">
      <Card title="Export provenance" subtitle="The immutable selection used to build this dataset."><dl><Row label="Format" value={dataset.format} /><Row label="Tasks" value={dataset.tasks.join(", ") || "All"} /><Row label="Data source" value={dataset.data_source} /><Row label="Collection batch" value={dataset.collection_batch_id ?? "—"} /><Row label="Include failures" value={dataset.include_failures ? "Yes" : "No"} /><Row label="Created by" value={dataset.created_by ?? "Unknown"} /><Row label="Exporter version" value={dataset.exporter_version} /></dl></Card>
      <Card title="Observation manifest" subtitle="Read-only schema sampled from the generated HDF5.">{dataset.status !== "ready" ? <Empty>Manifest is available after a successful build.</Empty> : fields.length === 0 ? <Empty>No schema fields were recorded.</Empty> : <div className="max-h-80 overflow-auto"><table className="w-full text-sm"><thead className="text-left text-xs uppercase text-ink-400"><tr><th className="pb-2">Path</th><th className="pb-2">Shape</th><th className="pb-2">Dtype</th></tr></thead><tbody>{fields.map((field) => <tr key={field.path} className="border-t border-ink-700/60"><td className="py-2 font-mono text-xs">{field.path}</td><td className="py-2 font-mono text-xs">[{field.shape.join(", ")}]</td><td className="py-2 text-xs text-ink-400">{field.dtype}</td></tr>)}</tbody></table>{Boolean(dataset.schema_manifest.masks?.length) && <p className="mt-3 text-xs text-ink-400">Masks: {dataset.schema_manifest.masks?.join(", ")}</p>}</div>}</Card>
    </div>

    <Card title="Episode inventory" subtitle={`${dataset.episodes.length} selected episode snapshots`}>
      {dataset.episodes.length === 0 ? <Empty>No episode inventory was recorded for this legacy dataset.</Empty> : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead className="text-left text-xs uppercase text-ink-400">
              <tr>
                <th className="pb-2 pr-6">Episode</th>
                <th className="px-3 pb-2">Source</th>
                <th className="px-3 pb-2">Task</th>
                <th className="px-3 pb-2">Outcome</th>
                <th className="px-3 pb-2 text-right">Frames</th>
                <th className="pb-2 pl-8">Review</th>
              </tr>
            </thead>
            <tbody>
              {dataset.episodes.map((episode) => (
                <tr key={`${episode.source}:${episode.episode_id}`} className="border-t border-ink-700/60">
                  <td className="py-2 pr-6">
                    <Link href={`/raw/${encodeURIComponent(episode.episode_id)}`} className="font-mono text-xs text-accent-500 hover:underline">
                      {episode.episode_id}
                    </Link>
                  </td>
                  <td className="px-3 py-2"><Badge tone={episode.source === "scripted" ? "info" : "neutral"}>{episode.source}</Badge></td>
                  <td className="px-3 py-2">{episode.task}</td>
                  <td className="px-3 py-2"><Badge tone={episode.outcome === "success" ? "ok" : episode.outcome === "failure" ? "bad" : "neutral"}>{episode.outcome}</Badge></td>
                  <td className="px-3 py-2 text-right tabular-nums">{episode.frames.toLocaleString()}</td>
                  <td className="py-2 pl-8"><Badge tone={episode.review_status === "approved" ? "ok" : "neutral"}>{episode.review_status}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  </div>;
}
