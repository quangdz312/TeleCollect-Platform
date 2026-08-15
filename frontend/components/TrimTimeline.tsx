"use client";

import { useCallback, useRef } from "react";
import { cx } from "@/components/ui";
import { frameTime } from "@/lib/format";

/**
 * Frame-accurate in/out selection over a recording.
 *
 * Trimming is non-destructive: this only edits two frame indices that travel
 * with the demonstration, and the crop is materialised once, at export.  So a
 * reviewer can always widen a trim again, and the raw recording stays exactly
 * as the operator produced it.
 */
export function TrimTimeline({
  numFrames,
  fps,
  start,
  end,
  playhead,
  successFrames,
  onChange,
  onSeek,
}: {
  numFrames: number;
  fps: number;
  start: number;
  end: number;
  playhead: number;
  successFrames?: boolean[];
  onChange: (start: number, end: number) => void;
  onSeek: (frame: number) => void;
}) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const dragging = useRef<"start" | "end" | "seek" | null>(null);

  const frameAt = useCallback(
    (clientX: number) => {
      const rect = trackRef.current?.getBoundingClientRect();
      if (!rect) return 0;
      const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
      return Math.round(ratio * (numFrames - 1));
    },
    [numFrames],
  );

  const apply = useCallback(
    (mode: "start" | "end" | "seek", frame: number) => {
      if (mode === "seek") return onSeek(frame);
      if (mode === "start") onChange(Math.min(frame, end - 1), end);
      else onChange(start, Math.max(frame, start + 1));
    },
    [start, end, onChange, onSeek],
  );

  const onPointerDown = (mode: "start" | "end" | "seek") => (event: React.PointerEvent) => {
    event.preventDefault();
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    dragging.current = mode;
    apply(mode, frameAt(event.clientX));
  };

  const onPointerMove = (event: React.PointerEvent) => {
    if (!dragging.current) return;
    apply(dragging.current, frameAt(event.clientX));
  };

  const onPointerUp = (event: React.PointerEvent) => {
    (event.currentTarget as HTMLElement).releasePointerCapture(event.pointerId);
    dragging.current = null;
  };

  const onHandleKeyDown = (mode: "start" | "end") => (event: React.KeyboardEvent) => {
    const current = mode === "start" ? start : end;
    let next: number | null = null;
    if (event.key === "ArrowLeft") next = current - 1;
    else if (event.key === "ArrowRight") next = current + 1;
    else if (event.key === "Home") next = mode === "start" ? 0 : start + 1;
    else if (event.key === "End") next = mode === "start" ? end - 1 : numFrames - 1;
    if (next === null) return;
    event.preventDefault();
    apply(mode, Math.min(Math.max(next, 0), numFrames - 1));
  };

  const pct = (frame: number) => `${(frame / Math.max(1, numFrames - 1)) * 100}%`;

  // Contiguous runs where the task's success predicate held, so the reviewer
  // can see at a glance when the episode actually completed.
  const successRuns: [number, number][] = [];
  if (successFrames) {
    let runStart: number | null = null;
    successFrames.forEach((flag, index) => {
      if (flag && runStart === null) runStart = index;
      if (!flag && runStart !== null) {
        successRuns.push([runStart, index]);
        runStart = null;
      }
    });
    if (runStart !== null) successRuns.push([runStart, successFrames.length]);
  }

  return (
    <div className="select-none">
      <div
        ref={trackRef}
        className="relative h-14 cursor-pointer rounded-lg border border-ink-700 bg-ink-850"
        onPointerDown={onPointerDown("seek")}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        {successRuns.map(([from, to]) => (
          <div
            key={`${from}-${to}`}
            className="absolute inset-y-0 bg-ok-600/25"
            style={{ left: pct(from), width: pct(to - from) }}
          />
        ))}

        <div className="absolute inset-y-0 left-0 bg-ink-950/70" style={{ width: pct(start) }} />
        <div
          className="absolute inset-y-0 right-0 bg-ink-950/70"
          style={{ width: pct(numFrames - 1 - end) }}
        />
        <div
          className="pointer-events-none absolute inset-y-0 border-x-2 border-accent-500/70 bg-accent-500/10"
          style={{ left: pct(start), width: pct(end - start) }}
        />

        <div
          className="pointer-events-none absolute inset-y-0 w-0.5 bg-white"
          style={{ left: pct(playhead) }}
        />

        {(["start", "end"] as const).map((handle) => (
          <div
            key={handle}
            role="slider"
            tabIndex={0}
            aria-label={handle === "start" ? "Trim start" : "Trim end"}
            aria-valuemin={handle === "start" ? 0 : start + 1}
            aria-valuemax={handle === "start" ? end - 1 : numFrames - 1}
            aria-valuenow={handle === "start" ? start : end}
            aria-valuetext={frameTime(handle === "start" ? start : end, fps)}
            onPointerDown={onPointerDown(handle)}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            onKeyDown={onHandleKeyDown(handle)}
            className={cx(
              "absolute top-0 h-full w-3 -translate-x-1/2 cursor-ew-resize rounded",
              "bg-accent-500 hover:bg-accent-400",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/80",
            )}
            style={{ left: pct(handle === "start" ? start : end) }}
            title={`${handle} of clip`}
          />
        ))}
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-3 text-xs text-ink-400">
        <span className="tabular">
          in <span className="text-ink-100">{start}</span> ({frameTime(start, fps)})
        </span>
        <span className="tabular">
          playhead <span className="text-ink-100">{playhead}</span>
        </span>
        <span className="tabular">
          out <span className="text-ink-100">{end}</span> ({frameTime(end, fps)})
        </span>
        <span className="tabular">
          kept <span className="text-ink-100">{end - start}</span> / {numFrames} frames ·{" "}
          {((end - start) / fps).toFixed(1)}s
        </span>
      </div>
    </div>
  );
}
