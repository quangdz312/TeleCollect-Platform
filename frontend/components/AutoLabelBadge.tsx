import { Badge } from "@/components/ui";
import type { AutoLabel } from "@/lib/api";

const labels: Record<AutoLabel, { tone: "ok" | "warn" | "bad"; text: string }> = {
  accept: { tone: "ok", text: "accept" },
  review: { tone: "warn", text: "review" },
  reject: { tone: "bad", text: "reject" },
};

export function AutoLabelBadge({ label, reason }: { label: AutoLabel; reason?: string }) {
  const item = labels[label] ?? labels.review;
  return <span title={reason}><Badge tone={item.tone}>{item.text}</Badge></span>;
}
