"use client";

import { Badge } from "@/components/ui";
import type { Demo } from "@/lib/api";

/** Where a recording sits in the review workflow, at a glance. */
export function StatusBadge({ demo }: { demo: Demo }) {
  if (demo.status === "approved") return <Badge tone="ok">approved · {demo.label}</Badge>;
  if (demo.status === "rejected") return <Badge tone="bad">rejected</Badge>;
  return <Badge tone={demo.auto_success ? "warn" : "neutral"}>needs review</Badge>;
}
