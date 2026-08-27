"use client";

/**
 * Chart pieces for the data-diversity report.
 *
 * Extracted from the standalone diversity page so the review screen can show
 * the same charts scoped to one collection batch — two copies of an SVG
 * scatter plot would drift apart the first time either was tweaked.
 */

import { useEffect, useState } from "react";
import { Empty, Select } from "@/components/ui";
import type { DiversityReport } from "@/lib/labeling";

const COLORS: Record<string, string> = {
  clean: "#2dd4bf", good: "#38bdf8", medium: "#fbbf24", poor: "#fb7185", unknown: "#94a3b8",
};

export function QualityChart({ rows }: { rows: DiversityReport["quality"] }) {
  const max = Math.max(1, ...rows.map((row) => row.total));
  return (
    <div className="space-y-3">
      {rows.map((row) => (
        <div key={row.quality} className="grid grid-cols-[70px_1fr_42px] items-center gap-3 text-xs">
          <span className="capitalize text-ink-300">{row.quality}</span>
          <div className="flex h-6 overflow-hidden rounded bg-ink-800" title={`${row.success} simulator successes, ${row.failure} failures`}>
            <div className="bg-ok-600" style={{ width: `${(row.success / max) * 100}%` }} />
            <div className="bg-bad-600" style={{ width: `${(row.failure / max) * 100}%` }} />
          </div>
          <span className="text-right tabular text-ink-300">{row.total}</span>
        </div>
      ))}
      <div className="flex gap-4 text-[11px] text-ink-400">
        <span><i className="mr-1 inline-block h-2 w-2 rounded-sm bg-ok-600" />sim success</span>
        <span><i className="mr-1 inline-block h-2 w-2 rounded-sm bg-bad-600" />sim failure</span>
      </div>
    </div>
  );
}

export function Scatter({ sets }: { sets: DiversityReport["position_sets"] }) {
  const [selected, setSelected] = useState(sets[0]?.key ?? "");
  useEffect(() => setSelected(sets[0]?.key ?? ""), [sets]);
  const points = sets.find((set) => set.key === selected)?.points ?? [];
  if (!points.length) return <Empty>No initial object positions are available.</Empty>;
  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const scale = (value: number, low: number, high: number, start: number, span: number) =>
    start + ((value - low) / Math.max(1e-6, high - low)) * span;
  return (
    <div>
      {sets.length > 1 && (
        <Select className="mb-3 max-w-40" value={selected} onChange={(event) => setSelected(event.target.value)}>
          {sets.map((set) => <option key={set.key} value={set.key}>{set.label}</option>)}
        </Select>
      )}
      <svg viewBox="0 0 560 250" className="w-full min-w-[360px] rounded-lg border border-ink-700 bg-ink-850">
        {[0, 1, 2, 3, 4].map((index) => <g key={index}>
          <line x1={45} x2={545} y1={20 + index * 52} y2={20 + index * 52} stroke="#dce3ec" />
          <line x1={45 + index * 125} x2={45 + index * 125} y1={20} y2={228} stroke="#dce3ec" />
        </g>)}
        {points.map((point) => (
          <circle key={point.episode_id} cx={scale(point.x, x0, x1, 55, 480)} cy={228 - scale(point.y, y0, y1, 10, 198)} r="4.5" fill={COLORS[point.quality] ?? COLORS.unknown} opacity="0.9">
            <title>{`${point.episode_id}\n${point.quality} · ${point.decision}\nX ${point.x.toFixed(3)}, Y ${point.y.toFixed(3)}`}</title>
          </circle>
        ))}
        <text x="280" y="246" fill="#64748b" fontSize="11">initial X (m)</text>
        <text x="8" y="125" fill="#64748b" fontSize="11" transform="rotate(-90 8 125)">initial Y (m)</text>
      </svg>
      <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-ink-400">
        {Object.entries(COLORS).filter(([key]) => key !== "unknown").map(([key, color]) => (
          <span key={key}><i className="mr-1 inline-block h-2 w-2 rounded-full" style={{ backgroundColor: color }} />{key}</span>
        ))}
      </div>
    </div>
  );
}

export function Histogram({ histogram }: { histogram: DiversityReport["length_histogram"] }) {
  if (!histogram.counts.length) return <Empty>No episode lengths are available.</Empty>;
  const max = Math.max(1, ...histogram.counts);
  return (
    <div className="flex h-52 items-end gap-2 border-b border-ink-600 px-2 pt-3">
      {histogram.counts.map((count, index) => (
        <div key={index} className="flex h-full flex-1 flex-col justify-end text-center">
          <span className="mb-1 text-[10px] tabular text-ink-400">{count}</span>
          <div className="min-h-1 rounded-t bg-accent-500/75" style={{ height: `${(count / max) * 82}%` }} title={`${histogram.edges[index]}–${histogram.edges[index + 1]} frames: ${count}`} />
          <span className="mt-1 truncate text-[9px] text-ink-400">{Math.round(histogram.edges[index])}</span>
        </div>
      ))}
    </div>
  );
}
