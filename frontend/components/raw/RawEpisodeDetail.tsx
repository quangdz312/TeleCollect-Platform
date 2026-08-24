"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { SynchronizedPlayback } from "@/components/raw/SynchronizedPlayback";
import { SignalTimeline } from "@/components/raw/SignalTimeline";
import { Alert, Badge, Button, Card, Empty, Stat } from "@/components/ui";
import { bytes, duration, timeAgo } from "@/lib/format";
import { RawApiError, rawApi, type RawEpisodeDetail as Detail } from "@/lib/raw";

function reviewTone(value: Detail["review_status"]): "ok" | "bad" | "warn" | "neutral" {
  if (value === "approved") return "ok";
  if (value === "rejected") return "bad";
  if (value === "pending") return "warn";
  return "neutral";
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-ink-700/60 py-2.5 last:border-0">
      <dt className="text-xs text-ink-400">{label}</dt>
      <dd className="max-w-[65%] break-all text-right text-sm font-medium">{value}</dd>
    </div>
  );
}

export function RawEpisodeDetail({ episodeId }: { episodeId: string }) {
  const { user, loading: authLoading } = useAuth();
  const [episode, setEpisode] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [playbackTime, setPlaybackTime] = useState(0);
  const [seekRequest, setSeekRequest] = useState<{ time: number; nonce: number } | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!user || user.role === "operator") return;
    setLoading(true);
    setError(null);
    try {
      setEpisode(await rawApi.detail(episodeId));
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Failed to load raw episode");
    } finally {
      setLoading(false);
    }
  }, [episodeId, user]);

  useEffect(() => {
    void load();
  }, [load]);

  const applyChange = async (operation: () => Promise<Detail>, message: string) => {
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      setEpisode(await operation());
      setSaved(message);
    } catch (problem) {
      if (problem instanceof RawApiError && problem.status === 409) {
        await load();
        setError(`${problem.message} Latest data has been reloaded.`);
      } else {
        setError(problem instanceof Error ? problem.message : "Could not update episode");
      }
    } finally {
      setSaving(false);
    }
  };

  if (authLoading || !user) return null;
  if (user.role === "operator") {
    return <Alert tone="info">Raw episode management is available to reviewers and administrators.</Alert>;
  }
  if (loading && !episode) return <Empty>Loading raw episode…</Empty>;
  if (error && !episode) {
    return <div className="space-y-3"><Alert>{error}</Alert><Link href="/raw"><Button variant="subtle">Back to inventory</Button></Link></div>;
  }
  if (!episode) return <Empty>Raw episode is unavailable.</Empty>;

  const outcome = episode.recorded_success === null
    ? "Unknown"
    : episode.recorded_success ? "Success" : "Failure";

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <Link href="/raw" className="text-xs text-accent-500 hover:underline">← Raw episodes</Link>
          <h1 className="mt-1 truncate font-heading text-[22px] font-bold tracking-tight">{episode.display_name}</h1>
          <p className="mt-0.5 break-all font-mono text-xs text-ink-400">{episode.episode_id}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Badge tone={episode.source === "scripted" ? "info" : "neutral"}>{episode.source}</Badge>
          <Badge>{episode.task}</Badge>
          <Badge tone={episode.recorded_success === true ? "ok" : episode.recorded_success === false ? "bad" : "neutral"}>{outcome}</Badge>
          <Badge tone={reviewTone(episode.review_status)}>{episode.review_status}</Badge>
        </div>
      </div>

      {error && <Alert>{error}</Alert>}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label="Frames" value={episode.length.toLocaleString()} hint={episode.control_hz ? `${episode.control_hz} Hz control` : "Control rate unavailable"} />
        <Stat label="Duration" value={episode.duration_s === null ? "—" : duration(episode.duration_s)} />
        <Stat label="Quality" value={episode.quality ?? "—"} hint={episode.source === "teleop" ? "Not assigned to teleop raw" : "Requested collection quality"} />
        <Stat label="Storage" value={episode.size_bytes === null ? "—" : bytes(episode.size_bytes)} hint="Episode-level size when available" />
      </div>

      <Card title="Camera playback" subtitle="Shared transport keeps the available camera views on one episode clock.">
        <SynchronizedPlayback episode={episode} onTimeChange={setPlaybackTime} seekRequest={seekRequest} />
      </Card>

      <Card title="Raw signal timeline" subtitle="Inspect actions and observations against the same clock as camera playback.">
        <SignalTimeline
          episode={episode}
          currentTime={playbackTime}
          onSeek={(time) => setSeekRequest({ time, nonce: Date.now() })}
        />
      </Card>

      <div className={`grid gap-5 ${user.role === "admin" ? "xl:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]" : ""}`}>
        {user.role === "admin" && (
          <Card title="Episode administration" subtitle={`Version ${episode.management_version} · raw artifacts remain read-only.`}>
            <div className="space-y-4">
              <p className="text-sm text-ink-400">Archive hides this episode from normal workflows without deleting its raw files. The action can be restored.</p>
              <Button
                variant="subtle"
                disabled={saving}
                onClick={() => {
                  const archiving = episode.review_status !== "archived";
                  if (archiving && !window.confirm("Archive this episode? Raw files will be preserved and the action can be restored.")) return;
                  void applyChange(
                    () => rawApi.archive(episode.episode_id, archiving, episode.management_version),
                    archiving ? "Episode archived." : "Episode restored.",
                  );
                }}
              >{episode.review_status === "archived" ? "Restore episode" : "Archive episode"}</Button>
              {saved && <Alert tone="ok">{saved}</Alert>}
            </div>
          </Card>
        )}

        <Card title="Audit history" subtitle="Newest management and review changes first.">
          {episode.audit.length === 0 ? <Empty>No management changes yet.</Empty> : (
            <div className="space-y-3">
              {episode.audit.map((entry) => (
                <div key={entry.id} className="rounded-lg border border-ink-700 bg-ink-850 px-4 py-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <Badge tone="info">{entry.action.replaceAll("_", " ")}</Badge>
                    <span className="text-xs text-ink-400">{new Date(entry.created_at).toLocaleString()}</span>
                  </div>
                  <p className="mt-2 text-sm">{entry.actor_name}</p>
                  <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-all rounded bg-ink-900 p-2 text-[11px] text-ink-400">{JSON.stringify(entry.changes, null, 2)}</pre>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)]">
        <Card title="Artifact manifest" subtitle="Read-only inventory; no raw file is modified from this page.">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[620px] text-sm">
              <thead className="text-left text-xs uppercase tracking-wider text-ink-400">
                <tr><th className="pb-2">File</th><th className="pb-2">Kind</th><th className="pb-2">Camera</th><th className="pb-2">Status</th><th className="pb-2 text-right">Size</th></tr>
              </thead>
              <tbody className="tabular">
                {episode.artifacts.map((artifact) => (
                  <tr key={`${artifact.kind}:${artifact.name}`} className="border-t border-ink-700/60">
                    <td className="py-3 font-mono text-xs">{artifact.name}</td>
                    <td className="py-3"><Badge>{artifact.kind}</Badge></td>
                    <td className="py-3 text-ink-400">{artifact.camera ?? "—"}</td>
                    <td className="py-3"><Badge tone={artifact.exists ? "ok" : "warn"}>{artifact.exists ? "Present" : "Missing"}</Badge></td>
                    <td className="py-3 text-right">{artifact.size_bytes === null ? "—" : bytes(artifact.size_bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <div className="space-y-5">
          <Card title="Metadata">
            <dl>
              <Row label="Source" value={episode.source} />
              <Row label="Task" value={episode.task} />
              <Row label="Operator" value={episode.operator_id ?? "—"} />
              <Row label="Collection batch" value={episode.collection_batch_id ?? "—"} />
              <Row label="Created" value={episode.created_at ? timeAgo(episode.created_at) : "—"} />
              <Row label="Control frequency" value={episode.control_hz ? `${episode.control_hz} Hz` : "—"} />
              <Row label="Artifact health" value={<Badge tone={episode.artifact_health === "healthy" ? "ok" : "warn"}>{episode.artifact_health}</Badge>} />
            </dl>
          </Card>
          <Card title="Review overview">
            <dl>
              <Row label="Simulator outcome" value={outcome} />
              <Row label="Quality" value={episode.quality ?? "—"} />
              <Row label="Review status" value={<Badge tone={reviewTone(episode.review_status)}>{episode.review_status}</Badge>} />
            </dl>
            <p className="mt-3 text-xs text-ink-400">Review status is read-only here. Use the Review page to approve or reject an episode.</p>
          </Card>
        </div>
      </div>
    </div>
  );
}
