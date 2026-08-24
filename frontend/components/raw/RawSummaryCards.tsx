import { Stat } from "@/components/ui";
import type { RawEpisodeSummary } from "@/lib/raw";

export function RawSummaryCards({ summary }: { summary: RawEpisodeSummary }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Stat label="Matching episodes" value={summary.total.toLocaleString()} hint={`${summary.pending} pending · ${summary.archived} archived`} />
      <Stat label="Teleop" value={summary.teleop.toLocaleString()} hint="Across all matching pages" />
      <Stat label="Scripted" value={summary.scripted.toLocaleString()} hint="Across all matching pages" />
      <Stat
        label="Simulator outcome"
        value={`${summary.successes} / ${summary.failures}`}
        hint="Success / failure across all matches"
        tone={summary.failures ? "warn" : undefined}
      />
    </div>
  );
}
