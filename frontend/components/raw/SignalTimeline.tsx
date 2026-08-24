"use client";

import { useEffect, useMemo, useState, type MouseEvent } from "react";
import { Alert, Badge, Button, Empty, Select } from "@/components/ui";
import { rawApi, type RawEpisodeDetail, type RawSignals } from "@/lib/raw";

const WIDTH = 900;
const HEIGHT = 280;
const PADDING = { left: 58, right: 20, top: 18, bottom: 38 };
const COLORS = ["#34d399", "#60a5fa", "#fbbf24", "#f472b6", "#a78bfa", "#22d3ee", "#fb7185", "#94a3b8", "#f97316"];
const FIELD_LABELS: Record<string, string> = {
  action: "Action",
  ee_pose: "End-effector pose",
  qpos: "Joint position",
  qvel: "Joint velocity",
  reward: "Reward",
};

function nearestIndex(values: number[], target: number) {
  if (!values.length) return 0;
  let low = 0;
  let high = values.length - 1;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (values[middle] < target) low = middle + 1;
    else high = middle;
  }
  if (low > 0 && Math.abs(values[low - 1] - target) < Math.abs(values[low] - target)) return low - 1;
  return low;
}

function number(value: number) {
  if (Math.abs(value) >= 100) return value.toFixed(1);
  if (Math.abs(value) >= 1) return value.toFixed(3);
  return value.toFixed(4);
}

export function SignalTimeline({
  episode,
  currentTime,
  onSeek,
}: {
  episode: RawEpisodeDetail;
  currentTime: number;
  onSeek: (time: number) => void;
}) {
  const [data, setData] = useState<RawSignals | null>(null);
  const [field, setField] = useState("action");
  const [viewMode, setViewMode] = useState<"simple" | "technical">("simple");
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setError(null);
    void rawApi.signals(episode.episode_id)
      .then((result) => {
        if (!active) return;
        setData(result);
        const first = ["action", "ee_pose", "qpos", "qvel", "reward"]
          .find((name) => result.signals[name]);
        if (first) setField(first);
      })
      .catch((problem) => {
        if (active) setError(problem instanceof Error ? problem.message : "Could not load raw signals");
      });
    return () => { active = false; };
  }, [episode.episode_id]);

  const chart = useMemo(() => {
    const series = data?.signals[field];
    if (!data || !series || !data.t.length) return null;
    const values = series.values.flat();
    let min = Math.min(...values);
    let max = Math.max(...values);
    if (!Number.isFinite(min) || !Number.isFinite(max)) return null;
    if (min === max) {
      const margin = Math.max(Math.abs(min) * 0.1, 1);
      min -= margin;
      max += margin;
    }
    const start = data.t[0];
    const end = data.t[data.t.length - 1];
    const innerWidth = WIDTH - PADDING.left - PADDING.right;
    const innerHeight = HEIGHT - PADDING.top - PADDING.bottom;
    const x = (time: number) => PADDING.left + ((time - start) / Math.max(end - start, 0.001)) * innerWidth;
    const y = (value: number) => PADDING.top + (1 - (value - min) / (max - min)) * innerHeight;
    const lines = series.labels.map((label, dimension) => ({
      label,
      color: COLORS[dimension % COLORS.length],
      points: series.values.map((row, index) => `${x(data.t[index])},${y(row[dimension] ?? 0)}`).join(" "),
    }));
    return { series, min, max, start, end, x, y, lines };
  }, [data, field]);

  const pointerTime = (event: MouseEvent<SVGSVGElement>) => {
    if (!chart) return 0;
    const bounds = event.currentTarget.getBoundingClientRect();
    const localX = ((event.clientX - bounds.left) / bounds.width) * WIDTH;
    const ratio = Math.max(0, Math.min(1, (localX - PADDING.left) / (WIDTH - PADDING.left - PADDING.right)));
    return chart.start + ratio * (chart.end - chart.start);
  };

  if (error) return <Alert>{error}</Alert>;
  if (!data) return <Empty>Loading raw signals…</Empty>;
  if (!chart) return <Empty>No plottable signal is available for this episode.</Empty>;

  const activeIndex = hoverIndex ?? nearestIndex(data.t, currentTime);
  const activeTime = data.t[activeIndex] ?? 0;
  const activeValues = chart.series.values[activeIndex] ?? [];
  const cursorTime = Math.max(chart.start, Math.min(currentTime, chart.end));
  const actionGroups = [
    { title: "Di chuyển XYZ", subtitle: "Lệnh dịch chuyển đầu gắp", dimensions: [0, 1, 2] },
    { title: "Xoay RX / RY / RZ", subtitle: "Lệnh thay đổi hướng đầu gắp", dimensions: [3, 4, 5] },
    { title: "Gripper", subtitle: "Trạng thái đóng hoặc mở kẹp", dimensions: [6] },
  ];
  const direction = (value: number, positive: string, negative: string) => {
    if (Math.abs(value) < 0.1) return "gần như đứng yên";
    return value > 0 ? positive : negative;
  };
  const gripper = activeValues[6] ?? 0;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <Select aria-label="Raw signal" value={field} onChange={(event) => setField(event.target.value)} className="w-52">
          {Object.keys(data.signals).map((name) => (
            <option key={name} value={name}>{FIELD_LABELS[name] ?? name}</option>
          ))}
        </Select>
        <Badge tone="info">{data.sampled_frames.length.toLocaleString()} samples</Badge>
        <span className="text-xs text-ink-400">Frame {data.sampled_frames[activeIndex]?.toLocaleString()} · {activeTime.toFixed(3)} s</span>
        {field === "action" && (
          <div className="ml-auto flex gap-1 rounded-lg border border-ink-700 p-1">
            <Button variant={viewMode === "simple" ? "primary" : "subtle"} onClick={() => setViewMode("simple")}>Dễ hiểu</Button>
            <Button variant={viewMode === "technical" ? "primary" : "subtle"} onClick={() => setViewMode("technical")}>Kỹ thuật</Button>
          </div>
        )}
      </div>

      {field === "action" && viewMode === "simple" && (
        <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_260px]">
          <div className="space-y-3">
            {actionGroups.map((group) => (
              <div key={group.title} className="overflow-hidden rounded-xl border border-tech-border bg-tech-bg">
                <div className="border-b border-tech-border px-4 py-2">
                  <div className="text-sm font-semibold text-tech-text">{group.title}</div>
                  <div className="text-[11px] text-tech-muted">{group.subtitle}</div>
                </div>
                <svg
                  viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
                  preserveAspectRatio="none"
                  role="img"
                  aria-label={`${group.title} timeline`}
                  className="block h-44 w-full cursor-crosshair"
                  onMouseMove={(event) => setHoverIndex(nearestIndex(data.t, pointerTime(event)))}
                  onMouseLeave={() => setHoverIndex(null)}
                  onClick={(event) => onSeek(pointerTime(event))}
                >
                  {[0, 0.5, 1].map((ratio) => {
                    const y = PADDING.top + ratio * (HEIGHT - PADDING.top - PADDING.bottom);
                    const value = chart.max - ratio * (chart.max - chart.min);
                    return <g key={ratio}><line x1={PADDING.left} x2={WIDTH - PADDING.right} y1={y} y2={y} stroke="#263449" /><text x={PADDING.left - 8} y={y + 4} textAnchor="end" fill="#8290a5" fontSize="11">{number(value)}</text></g>;
                  })}
                  {group.dimensions.map((dimension) => {
                    const line = chart.lines[dimension];
                    return line ? <polyline key={line.label} points={line.points} fill="none" stroke={line.color} strokeWidth="2" vectorEffect="non-scaling-stroke" /> : null;
                  })}
                  <line x1={chart.x(cursorTime)} x2={chart.x(cursorTime)} y1={PADDING.top} y2={HEIGHT - PADDING.bottom} stroke="#fff" strokeDasharray="4 4" opacity="0.85" />
                  {hoverIndex !== null && <line x1={chart.x(activeTime)} x2={chart.x(activeTime)} y1={PADDING.top} y2={HEIGHT - PADDING.bottom} stroke="#fbbf24" />}
                  <text x={PADDING.left} y={HEIGHT - 12} fill="#8290a5" fontSize="11">{chart.start.toFixed(2)} s</text>
                  <text x={WIDTH - PADDING.right} y={HEIGHT - 12} textAnchor="end" fill="#8290a5" fontSize="11">{chart.end.toFixed(2)} s</text>
                </svg>
                <div className="flex flex-wrap gap-3 border-t border-tech-border px-4 py-2">
                  {group.dimensions.map((dimension) => chart.lines[dimension] && (
                    <span key={dimension} className="flex items-center gap-1.5 text-xs text-ink-300">
                      <span className="h-2 w-2 rounded-full" style={{ backgroundColor: chart.lines[dimension].color }} />
                      {chart.lines[dimension].label}: <span className="font-mono">{number(activeValues[dimension] ?? 0)}</span>
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
          <aside className="h-fit rounded-xl border border-ink-700 bg-ink-850 p-4 xl:sticky xl:top-4">
            <h3 className="text-sm font-semibold">Trạng thái hiện tại</h3>
            <p className="mt-1 font-mono text-xs text-ink-400">Frame {data.sampled_frames[activeIndex]} · {activeTime.toFixed(2)} s</p>
            <dl className="mt-4 space-y-3 text-sm">
              <div><dt className="text-xs text-ink-400">Tiến / lùi (X)</dt><dd>{direction(activeValues[0] ?? 0, "đi theo chiều dương X", "đi theo chiều âm X")}</dd></div>
              <div><dt className="text-xs text-ink-400">Trái / phải (Y)</dt><dd>{direction(activeValues[1] ?? 0, "đi theo chiều dương Y", "đi theo chiều âm Y")}</dd></div>
              <div><dt className="text-xs text-ink-400">Lên / xuống (Z)</dt><dd>{direction(activeValues[2] ?? 0, "đi lên", "đi xuống")}</dd></div>
              <div><dt className="text-xs text-ink-400">Xoay</dt><dd>{Math.max(...activeValues.slice(3, 6).map(Math.abs), 0) < 0.1 ? "không đáng kể" : "đang thay đổi hướng"}</dd></div>
              <div><dt className="text-xs text-ink-400">Kẹp</dt><dd><Badge tone={gripper > 0 ? "warn" : "ok"}>{gripper > 0 ? "Đóng kẹp" : "Mở kẹp"}</Badge></dd></div>
            </dl>
            <p className="mt-4 text-[11px] text-ink-400">Giá trị gần 0 được diễn giải là gần như đứng yên. Dấu dương/âm biểu thị chiều điều khiển theo hệ tọa độ robot.</p>
          </aside>
        </div>
      )}

      {(field !== "action" || viewMode === "technical") && <div className="overflow-hidden rounded-xl border border-tech-border bg-tech-bg">
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="img"
          aria-label={`${FIELD_LABELS[field] ?? field} signal timeline`}
          className="block w-full cursor-crosshair"
          onMouseMove={(event) => setHoverIndex(nearestIndex(data.t, pointerTime(event)))}
          onMouseLeave={() => setHoverIndex(null)}
          onClick={(event) => onSeek(pointerTime(event))}
        >
          {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
            const y = PADDING.top + ratio * (HEIGHT - PADDING.top - PADDING.bottom);
            const value = chart.max - ratio * (chart.max - chart.min);
            return <g key={ratio}><line x1={PADDING.left} x2={WIDTH - PADDING.right} y1={y} y2={y} stroke="#263449" strokeWidth="1" /><text x={PADDING.left - 8} y={y + 4} textAnchor="end" fill="#8290a5" fontSize="11">{number(value)}</text></g>;
          })}
          {chart.lines.map((line) => <polyline key={line.label} points={line.points} fill="none" stroke={line.color} strokeWidth="1.8" vectorEffect="non-scaling-stroke" />)}
          <line x1={chart.x(cursorTime)} x2={chart.x(cursorTime)} y1={PADDING.top} y2={HEIGHT - PADDING.bottom} stroke="#ffffff" strokeDasharray="4 4" opacity="0.8" />
          {hoverIndex !== null && <line x1={chart.x(activeTime)} x2={chart.x(activeTime)} y1={PADDING.top} y2={HEIGHT - PADDING.bottom} stroke="#fbbf24" opacity="0.9" />}
          <text x={PADDING.left} y={HEIGHT - 12} fill="#8290a5" fontSize="11">{chart.start.toFixed(2)} s</text>
          <text x={WIDTH - PADDING.right} y={HEIGHT - 12} textAnchor="end" fill="#8290a5" fontSize="11">{chart.end.toFixed(2)} s</text>
        </svg>
      </div>}

      {(field !== "action" || viewMode === "technical") && <div className="flex flex-wrap gap-x-4 gap-y-1">
        {chart.lines.map((line, index) => (
          <span key={line.label} className="flex items-center gap-1.5 text-xs text-ink-300">
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: line.color }} />
            {line.label}: <span className="font-mono">{number(activeValues[index] ?? 0)}</span>
          </span>
        ))}
      </div>}

      <p className="text-[11px] text-ink-400">
        Click the chart to seek all video views. The white cursor follows playback.
        {data.time_basis === "scripted_playback" && ` Scripted samples are recorded at ${data.control_hz} Hz and mapped to composite playback${data.video_stride > 1 ? ` with stride ${data.video_stride}` : ""}.`}
      </p>
    </div>
  );
}
