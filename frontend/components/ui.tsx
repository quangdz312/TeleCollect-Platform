"use client";

import { useState, type ReactNode } from "react";

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
        "slide-up rounded-2xl border border-ink-700 bg-ink-900 shadow-[0_4px_14px_rgba(15,23,42,0.05),0_1px_3px_rgba(15,23,42,0.06)] transition-shadow duration-200 hover:shadow-[0_8px_20px_rgba(15,23,42,0.06)]",
        className,
      )}
    >
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-700 px-5 py-4">
          <div>
            {title && (
              <h2 className="font-heading text-[15px] font-bold tracking-tight">{title}</h2>
            )}
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
  primary:
    "bg-accent-500 hover:bg-accent-400 text-white border-transparent shadow-[0_6px_14px_rgba(37,99,235,0.22)]",
  success:
    "bg-ok-600 hover:bg-ok-400 text-white border-transparent shadow-[0_6px_14px_rgba(5,150,105,0.2)]",
  danger:
    "bg-bad-600 hover:bg-bad-400 text-white border-transparent shadow-[0_6px_14px_rgba(220,38,38,0.2)]",
  ghost: "bg-transparent hover:bg-accent-500/10 text-accent-500 border-ink-600",
  subtle: "bg-ink-850 hover:bg-ink-800 text-ink-100 border-ink-700",
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
        "inline-flex items-center justify-center gap-1.5 rounded-lg border px-3.5 py-1.5 text-sm font-semibold",
        "transition-all active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/60 focus-visible:ring-offset-2 focus-visible:ring-offset-ink-950",
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
    neutral: "bg-ink-850 text-ink-300 border-ink-700",
    ok: "bg-ok-600/10 text-ok-600 border-ok-600/30",
    warn: "bg-warn-400/10 text-warn-400 border-warn-400/30",
    bad: "bg-bad-600/10 text-bad-600 border-bad-600/30",
    info: "bg-accent-500/10 text-accent-500 border-accent-500/30",
  };
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 rounded-md border px-2.5 py-1 text-[11px] font-semibold tracking-wide",
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
    <div className="slide-up rounded-xl border border-ink-700 bg-ink-900 px-4 py-4 shadow-[0_1px_2px_rgba(15,23,42,0.04)] transition-transform duration-200 hover:-translate-y-0.5">
      <div className="text-[11px] font-bold uppercase tracking-wider text-ink-400">{label}</div>
      <div
        className={cx(
          "font-heading mt-2 text-[26px] font-bold tracking-tight tabular",
          toneClass,
        )}
      >
        {value}
      </div>
      {hint && <div className="mt-1.5 text-xs text-ink-400">{hint}</div>}
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
  className,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={cx("block", className)}>
      <span className="mb-1 block text-xs font-medium text-ink-300">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-ink-400">{hint}</span>}
    </label>
  );
}

/**
 * Một nhóm trường có tiêu đề, dùng cho form dài.
 *
 * Một lưới phẳng gồm mười mấy ô bắt người dùng đọc hết mới biết ô nào cần đổi;
 * chia nhóm cho thấy ngay đâu là phần thường sửa và đâu là phần để mặc định.
 */
export function FieldGroup({
  title,
  hint,
  children,
  columns = 4,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
  columns?: 2 | 3 | 4;
}) {
  const grid = { 2: "sm:grid-cols-2", 3: "sm:grid-cols-2 lg:grid-cols-3", 4: "sm:grid-cols-2 lg:grid-cols-4" }[columns];
  return (
    <fieldset className="min-w-0">
      <legend className="mb-1 text-xs font-semibold uppercase tracking-wide text-ink-300">{title}</legend>
      {hint && <p className="mb-3 text-[11px] text-ink-400">{hint}</p>}
      <div className={cx("grid gap-3", grid, !hint && "mt-3")}>{children}</div>
    </fieldset>
  );
}

/** Nhóm trường mặc định gập lại — các tham số hiếm khi phải đổi. */
export function AdvancedGroup({
  title,
  children,
  columns = 4,
}: {
  title: string;
  children: ReactNode;
  columns?: 2 | 3 | 4;
}) {
  const grid = { 2: "sm:grid-cols-2", 3: "sm:grid-cols-2 lg:grid-cols-3", 4: "sm:grid-cols-2 lg:grid-cols-4" }[columns];
  return (
    <details className="group min-w-0 rounded-lg border border-ink-700 px-3 py-2">
      <summary className="cursor-pointer list-none text-xs font-semibold uppercase tracking-wide text-ink-300 outline-none focus-visible:ring-2 focus-visible:ring-accent-500/40">
        <span className="mr-1 inline-block transition-transform group-open:rotate-90">›</span>
        {title}
      </summary>
      <div className={cx("mt-3 grid gap-3", grid)}>{children}</div>
    </details>
  );
}

const CONTROL =
  "w-full rounded-lg border border-ink-600 bg-ink-900 px-3 py-1.5 text-sm text-ink-100 " +
  "outline-none transition-colors hover:border-ink-400/60 focus:border-accent-500 focus:ring-2 focus:ring-accent-500/15";

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
    bad: "border-bad-600/30 bg-bad-600/10 text-bad-600",
    ok: "border-ok-600/30 bg-ok-600/10 text-ok-600",
    info: "border-accent-500/30 bg-accent-500/10 text-accent-500",
  };
  return (
    <div className={cx("fade-in rounded-lg border px-3 py-2 text-sm", tones[tone])}>{children}</div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cx("motion-safe:animate-pulse rounded-md bg-ink-700/50", className)}
    />
  );
}

/** Fixed-aspect thumbnail with a neutral placeholder on missing/broken image; no retry. */
export function Thumbnail({ src, alt, className }: { src: string; alt: string; className?: string }) {
  const [failed, setFailed] = useState(false);
  return (
    <div
      className={cx(
        "flex aspect-video w-full items-center justify-center overflow-hidden rounded-md bg-ink-800",
        className,
      )}
    >
      {failed ? (
        <span aria-hidden="true" className="text-ink-500">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <rect x="3" y="5" width="18" height="14" rx="2" />
            <circle cx="9" cy="10" r="1.5" />
            <path d="M21 16l-5.5-5.5a1.5 1.5 0 0 0-2.1 0L3 21" />
          </svg>
        </span>
      ) : (
        <img
          src={src}
          alt={alt}
          loading="lazy"
          className="h-full w-full object-cover"
          onError={() => setFailed(true)}
        />
      )}
    </div>
  );
}

/** File-picker card matching frontend_demo's `.dropzone` (icon, title, required/optional tag). */
export function Dropzone({
  icon,
  title,
  tag,
  required,
  fileName,
  inputProps,
}: {
  icon: ReactNode;
  title: string;
  tag: string;
  required?: boolean;
  fileName?: string | null;
  inputProps: React.InputHTMLAttributes<HTMLInputElement>;
}) {
  return (
    <div
      className={cx(
        "rounded-xl border-[1.5px] border-dashed p-4 text-center transition-colors",
        required ? "border-accent-500/40" : "border-ink-600",
        "bg-ink-850 hover:border-accent-500 hover:bg-accent-500/5",
      )}
    >
      <div className="mx-auto mb-2.5 grid h-10 w-10 place-items-center rounded-[10px] bg-accent-500/10 text-accent-500">
        {icon}
      </div>
      <div className="text-[13px] font-bold text-ink-100">{title}</div>
      <div className="mt-0.5 text-[10px] font-bold uppercase tracking-wider text-ink-400">{tag}</div>
      <label className="mt-3 block text-left">
        <input
          {...inputProps}
          type="file"
          className={cx(
            "block w-full cursor-pointer text-xs text-ink-300 file:mr-2.5 file:cursor-pointer file:rounded-md file:border file:border-ink-600 file:bg-ink-900 file:px-2.5 file:py-1 file:text-xs file:font-semibold file:text-ink-100 hover:file:bg-ink-800 hover:file:border-accent-500",
            inputProps.className,
          )}
        />
      </label>
      {fileName && <div className="mt-1.5 truncate text-[11px] text-accent-500">{fileName}</div>}
    </div>
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
