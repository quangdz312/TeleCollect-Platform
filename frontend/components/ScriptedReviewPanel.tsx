"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Badge, Button } from "@/components/ui";
import { AutoLabelBadge } from "@/components/AutoLabelBadge";
import { labeling, type Episode } from "@/lib/labeling";
import type { DemoStatus, LabelValue } from "@/lib/api";

export function ScriptedReviewRows({
  task,
  status,
  label,
  quality,
  collectionBatchId,
  returnTo,
  onCount,
}: {
  task?: string;
  status?: DemoStatus;
  label?: LabelValue;
  quality?: string;
  collectionBatchId?: string;
  returnTo: string;
  onCount?: (count: number) => void;
}) {
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const [loading, setLoading] = useState(true);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const scriptedStatus = status === "approved" || status === "rejected"
        ? "reviewed"
        : status === "recorded" || status === "labeled"
          ? "pending"
          : "all";
      const result = await labeling.episodes({
        task,
        quality,
        collectionBatchId,
        status: scriptedStatus,
        includeScore: Boolean(label),
      });
      const filtered = result.episodes.filter((episode) => {
        if (status === "approved" && episode.label?.human_decision !== "approved") return false;
        if (status === "rejected" && episode.label?.human_decision !== "rejected") return false;
        if (label === "success" && episode.recorded_success !== true) return false;
        if (label === "failure" && episode.recorded_success !== false) return false;
        return true;
      });
      setEpisodes(filtered);
      onCount?.(filtered.length);
    } finally {
      setLoading(false);
    }
  }, [collectionBatchId, label, onCount, quality, status, task]);
  useEffect(() => { void load(); }, [load]);

  return <>
    {loading && (
      <tr className="border-t border-ink-700/50">
        <td colSpan={9} className="py-6 text-center text-sm text-ink-400">Đang tải episode scripted…</td>
      </tr>
    )}
    {!loading && episodes.length === 0 && (
      <tr className="border-t border-ink-700/50">
        <td colSpan={9} className="py-6 text-center text-sm text-ink-400">Chưa có episode scripted.</td>
      </tr>
    )}
    {episodes.map((episode) => (
      <tr key={episode.episode_id} className="border-t border-ink-700/50 hover:bg-ink-850/50">
        <td className="py-2"><div className="flex items-center gap-2"><span className="truncate font-mono text-xs">{episode.display_name || episode.demo}</span><Badge tone="info">Scripted</Badge></div><div className="mt-1 text-xs text-ink-400">{episode.task}</div></td>
        <td className="py-2 text-ink-300">—</td>
        <td className="py-2 text-ink-400">—</td>
        <td className="py-2 text-right">{(episode.length / 30).toFixed(1)}s</td>
        <td className="py-2 text-right">{episode.length}</td>
        <td className="py-2 pl-4 text-right">—</td>
        <td className="py-2 pl-5"><AutoLabelBadge label={episode.auto_label} reason={episode.auto_label_reason} /></td>
        <td className="py-2 pl-5"><div className="flex flex-wrap gap-1"><Badge tone={episode.label ? (episode.label.human_decision === "approved" ? "ok" : "bad") : "warn"}>{episode.label?.human_decision ?? (episode.audit_required ? "audit" : "needs review")}</Badge>{episode.label?.decision_source === "auto_gate" && <Badge tone="info">auto</Badge>}</div></td>
        <td className="py-2 text-right"><Link href={`/review/${encodeURIComponent(episode.episode_id)}?source=scripted&returnTo=${encodeURIComponent(returnTo)}`}><Button variant="subtle">Open</Button></Link></td>
      </tr>
    ))}
  </>;
}
