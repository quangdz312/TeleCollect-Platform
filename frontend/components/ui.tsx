"use client";

import type { ReactNode } from "react";

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ");
}

export function Card({
  title,
  subtitle,
  actions,
  children,
  className,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cx(
        "rounded-xl border border-ink-700/70 bg-ink-900/70 backdrop-blur-sm shadow-lg shadow-black/20",
        className,
      )}
    >
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-700/60 px-5 py-3.5">
          <div>
            {title && <h2 className="text-sm font-semibold tracking-wide">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-400">{subtitle}</p>}
          </div>
          {actions && <div className="flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

type ButtonVariant = "primary" | "ghost" | "danger" | "success" | "subtle";

const BUTTON_STYLES: Record<ButtonVariant, string> = {
  primary: "bg-accent-500 hover:bg-accent-400 text-white border-transparent",
  success: "bg-ok-600 hover:bg-ok-400 text-white border-transparent",
  danger: "bg-bad-600 hover:bg-bad-400 text-white border-transparent",
  ghost: "bg-transparent hover:bg-ink-800 text-ink-100 border-ink-600",
  subtle: "bg-ink-800 hover:bg-ink-700 text-ink-100 border-ink-700",
};

export function Button({
  variant = "ghost",
  className,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return (
    <button
      {...props}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-medium",
        "transition-colors disabled:cursor-not-allowed disabled:opacity-40",
        BUTTON_STYLES[variant],
        className,
      )}
    />
  );
}

export function Badge({
  tone = "neutral",
  children,
}: {
  tone?: "neutral" | "ok" | "warn" | "bad" | "info";
  children: ReactNode;
}) {
  const tones = {
    neutral: "bg-ink-700/60 text-ink-300 border-ink-600",
    ok: "bg-ok-600/15 text-ok-400 border-ok-600/40",
    warn: "bg-warn-400/15 text-warn-400 border-warn-400/40",
    bad: "bg-bad-600/15 text-bad-400 border-bad-600/40",
    info: "bg-accent-500/15 text-accent-400 border-accent-500/40",
  };
  return (
    <span
      className={cx(
        "inline-flex items-center rounded-md border px-2 py-0.5 text-[11px] font-medium",
        tones[tone],
      )}
    >
      {children}
    </span>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "ok" | "warn" | "bad";
}) {
  const toneClass =
    tone === "ok"
      ? "text-ok-400"
      : tone === "warn"
        ? "text-warn-400"
        : tone === "bad"
          ? "text-bad-400"
          : "text-ink-100";
  return (
    <div className="rounded-lg border border-ink-700/60 bg-ink-850/60 px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-ink-400">{label}</div>
      <div className={cx("mt-1 text-2xl font-semibold tabular", toneClass)}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-ink-400">{hint}</div>}
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-ink-300">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-ink-400">{hint}</span>}
    </label>
  );
}

const CONTROL =
  "w-full rounded-lg border border-ink-600 bg-ink-850 px-3 py-1.5 text-sm text-ink-100 " +
  "outline-none focus:border-accent-500 focus:ring-1 focus:ring-accent-500/40";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cx(CONTROL, props.className)} />;
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cx(CONTROL, props.className)} />;
}

export function TextArea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={cx(CONTROL, "min-h-20", props.className)} />;
}

export function Alert({
  tone = "bad",
  children,
}: {
  tone?: "bad" | "ok" | "info";
  children: ReactNode;
}) {
  const tones = {
    bad: "border-bad-600/40 bg-bad-600/10 text-bad-400",
    ok: "border-ok-600/40 bg-ok-600/10 text-ok-400",
    info: "border-accent-500/40 bg-accent-500/10 text-accent-400",
  };
  return (
    <div className={cx("rounded-lg border px-3 py-2 text-sm", tones[tone])}>{children}</div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-ink-700 px-4 py-10 text-center text-sm text-ink-400">
      {children}
    </div>
  );
}

/** Minimal dependency-free line chart. */
export function Sparkline({
  series,
  height = 140,
  yLabel,
  xLabel,
}: {
  series: { name: string; color: string; points: [number, number][] }[];
  height?: number;
  yLabel?: string;
  xLabel?: string;
}) {
  const all = series.flatMap((s) => s.points);
  if (all.length < 2) return <Empty>Not enough data points yet.</Empty>;

  const xs = all.map((p) => p[0]);
  const ys = all.map((p) => p[1]);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  const width = 640;
  const pad = { top: 8, right: 8, bottom: 20, left: 44 };
  const sx = (x: number) =>
    pad.left + ((x - x0) / Math.max(1e-9, x1 - x0)) * (width - pad.left - pad.right);
  const sy = (y: number) =>
    height - pad.bottom - ((y - y0) / Math.max(1e-9, y1 - y0)) * (height - pad.top - pad.bottom);

  return (
    <div className="w-full overflow-x-auto">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full min-w-[420px]">
        {[0, 0.5, 1].map((t) => (
          <g key={t}>
            <line
              x1={pad.left}
              x2={width - pad.right}
              y1={sy(y0 + t * (y1 - y0))}
              y2={sy(y0 + t * (y1 - y0))}
              stroke="#212a39"
            />
            <text
              x={pad.left - 6}
              y={sy(y0 + t * (y1 - y0)) + 3}
              textAnchor="end"
              className="fill-ink-400 text-[9px]"
            >
              {(y0 + t * (y1 - y0)).toFixed(3)}
            </text>
          </g>
        ))}
        {series.map((s) => (
          <polyline
            key={s.name}
            fill="none"
            stroke={s.color}
            strokeWidth={1.6}
            points={s.points.map(([x, y]) => `${sx(x)},${sy(y)}`).join(" ")}
          />
        ))}
        {xLabel && (
          <text x={width / 2} y={height - 4} textAnchor="middle" className="fill-ink-400 text-[9px]">
            {xLabel}
          </text>
        )}
        {yLabel && (
          <text x={4} y={12} className="fill-ink-400 text-[9px]">
            {yLabel}
          </text>
        )}
      </svg>
      <div className="mt-1 flex flex-wrap gap-3 text-[11px] text-ink-400">
        {series.map((s) => (
          <span key={s.name} className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: s.color }} />
            {s.name}
          </span>
        ))}
      </div>
    </div>
  );
}
