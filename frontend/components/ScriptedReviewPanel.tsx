"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Badge, Button } from "@/components/ui";
import { AutoLabelBadge } from "@/components/AutoLabelBadge";
import { labeling, type Episode } from "@/lib/labeling";

export function ScriptedReviewRows() {
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  const load = useCallback(async () => {
    setEpisodes((await labeling.episodes({ status: "all", includeScore: false })).episodes);
  }, []);
  useEffect(() => { void load(); }, [load]);

  return <>
    {episodes.map((episode) => (
      <tr key={episode.episode_id} className="border-t border-ink-700/50 hover:bg-ink-850/50">
        <td className="py-2"><div className="flex items-center gap-2"><span className="truncate font-mono text-xs">{episode.display_name || episode.demo}</span><Badge tone="info">Scripted</Badge></div><div className="mt-1 text-xs text-ink-400">{episode.task}</div></td>
        <td className="py-2 text-ink-300">—</td>
        <td className="py-2 text-ink-400">—</td>
        <td className="py-2 text-right">{(episode.length / 30).toFixed(1)}s</td>
        <td className="py-2 text-right">{episode.length}</td>
        <td className="py-2 pl-4 text-right">—</td>
        <td className="py-2 pl-5"><AutoLabelBadge label={episode.auto_label} reason={episode.auto_label_reason} /></td>
        <td className="py-2 pl-5"><Badge tone={episode.label ? "ok" : "warn"}>{episode.label?.human_decision ?? "needs review"}</Badge></td>
        <td className="py-2 text-right"><Link href={`/review/${encodeURIComponent(episode.episode_id)}?source=scripted`}><Button variant="subtle">Open</Button></Link></td>
      </tr>
    ))}
  </>;
}
