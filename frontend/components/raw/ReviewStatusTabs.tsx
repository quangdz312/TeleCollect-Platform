"use client";

/**
 * Review-status shortcut above the episode table.
 *
 * The old review page only ever listed what still needed a decision, so that
 * view disappeared when review folded into this page. These tabs bring it
 * back as one click — "Needs review" is the working queue, and the counts make
 * the remaining workload visible without opening the filter panel.
 */

import { cx } from "@/components/ui";
import type { RawEpisodeReviewStatus, RawEpisodeSummary } from "@/lib/raw";

/** `diversity` is a view, not a status — it swaps the table for the report. */
export const DIVERSITY_VIEW = "diversity";

type Value = RawEpisodeReviewStatus | "" | typeof DIVERSITY_VIEW;

const TABS: { value: Value; label: string; key: keyof RawEpisodeSummary | null }[] = [
  { value: "", label: "All", key: null },
  { value: "pending", label: "Needs review", key: "pending" },
  { value: "approved", label: "Approved", key: "approved" },
  { value: "rejected", label: "Rejected", key: "rejected" },
  { value: "archived", label: "Archived", key: "archived" },
];

export function ReviewStatusTabs({
  value,
  counts,
  onChange,
}: {
  value: Value;
  counts: RawEpisodeSummary;
  onChange: (value: Value) => void;
}) {
  const rowClass = (active: boolean) =>
    cx(
      "flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2 text-sm font-semibold transition-colors",
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/60",
      active
        ? "border-accent-500 bg-accent-500/10 text-accent-500"
        : "border-ink-700 bg-ink-900 text-ink-300 hover:border-ink-400/60 hover:text-ink-100",
    );
  return (
    <div role="tablist" aria-label="Review status" className="flex flex-col gap-1.5">
      {TABS.map((tab) => {
        const count = tab.key === null ? counts.total : counts[tab.key];
        const active = value === tab.value;
        return (
          <button
            key={tab.value || "all"}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(tab.value)}
            className={rowClass(active)}
          >
            <span>{tab.label}</span>
            <span className="tabular-nums text-xs text-ink-400">
              {typeof count === "number" ? count.toLocaleString() : 0}
            </span>
          </button>
        );
      })}

      <button
        type="button"
        role="tab"
        aria-selected={value === DIVERSITY_VIEW}
        onClick={() => onChange(DIVERSITY_VIEW)}
        className={cx(rowClass(value === DIVERSITY_VIEW), "mt-1.5 border-dashed")}
      >
        <span>Data diversity</span>
      </button>
    </div>
  );
}
