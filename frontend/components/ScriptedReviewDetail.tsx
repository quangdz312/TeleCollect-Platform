"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, Badge, Button, Card, Empty, Field, TextArea } from "@/components/ui";
import { AutoLabelBadge } from "@/components/AutoLabelBadge";
import { labeling, type Episode } from "@/lib/labeling";

export function ScriptedReviewDetail({ episodeId, returnTo }: { episodeId: string; returnTo: string }) {
  const [episode, setEpisode] = useState<Episode | null>(null);
  const [videoReady, setVideoReady] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const load = useCallback(async () => { setEpisode(await labeling.detail(episodeId, false)); setVideoReady((await labeling.requestVideo(episodeId)).status === "ready"); }, [episodeId]);
  useEffect(() => { void load().catch((problem) => setError((problem as Error).message)); }, [load]);
  const submit = async (decision: "approved" | "rejected") => {
    if (decision === "rejected" && !note.trim()) { setError("Khi reject scripted episode, hãy ghi lý do trong Note."); return; }
    try { await labeling.submitLabel({ episode_id: episodeId, decision, reasons: [], note, reviewer: "web", blind: true }); setSaved(decision === "approved" ? "Accepted." : "Rejected."); await load(); } catch (problem) { setError((problem as Error).message); }
  };
  if (error && !episode) return <Alert>{error}</Alert>;
  if (!episode) return <Empty>Loading scripted episode…</Empty>;
  return <div className="space-y-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><a href={returnTo} className="text-xs text-accent-400 hover:underline">← Review queue</a><h1 className="mt-1 text-lg font-semibold">{episode.task}</h1><p className="text-sm text-ink-400">{episode.display_name || episode.demo} · {episode.length} frames · scripted collection</p></div><div className="flex flex-wrap gap-2"><Badge tone="info">Scripted</Badge><AutoLabelBadge label={episode.auto_label} reason={episode.auto_label_reason} /><Badge tone={episode.label ? (episode.label.human_decision === "approved" ? "ok" : "bad") : "warn"}>{episode.label?.human_decision ?? (episode.audit_required ? "audit required" : "needs review")}</Badge>{episode.label?.decision_source === "auto_gate" && <Badge tone="info">auto gate</Badge>}</div></div>
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]"><Card title="Playback">{videoReady ? <video src={labeling.videoUrl(episodeId)} controls className="aspect-square w-full rounded-lg border border-ink-700 bg-black" /> : <Empty>Video đang render. Bấm refresh sau ít giây.</Empty>}</Card><div className="space-y-5"><Card title="Verdict"><div className="space-y-3"><div className="flex gap-2"><Badge tone="info">Scripted</Badge><Badge>{episode.task}</Badge><Badge>{episode.length} frames</Badge></div>{episode.audit_required && <Alert tone="info">Episode này đã đủ điều kiện auto-pass nhưng được giữ lại trong mẫu audit. Hãy xem video và quyết định như bình thường.</Alert>}{episode.label?.decision_source === "auto_gate" && <Alert tone="info">Quyết định tự động: {episode.label.gate_reason || episode.auto_gate_reason}. Bạn có thể override bằng nút bên dưới.</Alert>}<Field label="Reviewer notes"><TextArea value={note} onChange={(event) => setNote(event.target.value)} placeholder="Note / lý do reject" /></Field><div className="grid grid-cols-2 gap-2"><Button variant="success" onClick={() => void submit("approved")}>Accept</Button><Button variant="danger" onClick={() => void submit("rejected")}>Reject</Button></div><Button variant="subtle" className="w-full" onClick={() => void load()}>Refresh video</Button>{error && <Alert>{error}</Alert>}{saved && <Alert tone="ok">{saved}</Alert>}</div></Card><Card title="Capture format"><dl className="space-y-1.5 text-xs"><Row label="Source" value="Scripted" /><Row label="Resolution" value="480 × 480" /><Row label="Codec" value="H.264 / yuv420p" /><Row label="Frames" value={String(episode.length)} /></dl></Card></div></div>
  </div>;
}
function Row({ label, value }: { label: string; value: string }) { return <div className="flex justify-between gap-3"><dt className="text-ink-400">{label}</dt><dd>{value}</dd></div>; }

/** Where the suggested trim falls, as a band over the episode's full length.
 *
 * The video itself no longer dims the trimmed frames. It is now encoded while
 * the episode is being collected, which is before scoring has run, so the
 * trim simply does not exist yet at encode time. Drawing it here keeps the
 * suggestion visible and, unlike burnt-in shading, lets it change when the
 * episode is rescored without re-encoding anything.
 */
function TrimBar({ episode }: { episode: Episode }) {
  const total = episode.length;
  if (!total) return null;
  const start = Math.max(0, Math.min(episode.suggested_trim_start, total));
  const end = Math.max(start, Math.min(episode.suggested_trim_end, total));
  const pct = (value: number) => `${(value / total) * 100}%`;
  return (
    <div className="mt-2">
      <div className="relative h-2 w-full overflow-hidden rounded bg-ink-800" title={`Suggested keep: frames ${start}–${end} of ${total}`}>
        <div className="absolute inset-y-0 bg-accent-500/70" style={{ left: pct(start), width: pct(end - start) }} />
      </div>
      <p className="mt-1 text-[11px] text-ink-400">
        Suggested trim keeps frames {start}–{end} of {total}; the shaded band is what survives.
      </p>
    </div>
  );
}
