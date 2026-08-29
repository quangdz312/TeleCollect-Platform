"use client";

/**
 * The collection batch the desktop app is currently filing episodes into.
 *
 * A batch belongs to exactly one task, and the app already refuses a capture
 * whose task disagrees with the active batch — but only after the operator has
 * chosen one and pressed start. Reading the batch here lets the collection
 * screens pin the task up front, so the mismatch cannot be set up at all.
 *
 * The endpoint exists only inside the app. On the web it 404s, the hook returns
 * null, and the screens keep their free task picker.
 */

import { useEffect, useState } from "react";

export interface ActiveBatch {
  id: string;
  name: string;
  /** The app's own task name, e.g. `lift_cube`. */
  task: string;
}

/**
 * The app names tasks after the robosuite environment (`lift_cube`), while the
 * collection API names them after the simulator task (`lift`). Same mapping the
 * app uses to compare the two.
 */
const APP_TO_API: Record<string, string> = {
  lift_cube: "lift",
  pick_place_can: "can",
  nut_assembly_square: "square",
  tool_hang: "tool_hang",
};

export function apiTaskOf(batch: ActiveBatch | null): string | null {
  if (!batch) return null;
  return APP_TO_API[batch.task] ?? batch.task;
}

/**
 * The app's batch bar dispatches this after switching batches. These screens
 * are React and the bar is plain DOM injected around them, so an event is the
 * only thing that crosses between the two.
 */
export const ACTIVE_BATCH_CHANGED = "telecollect:active-batch-changed";

export function useActiveBatch(): ActiveBatch | null {
  const [batch, setBatch] = useState<ActiveBatch | null>(null);

  useEffect(() => {
    let cancelled = false;

    const read = () => {
      fetch("/api/v1/local/batches/active")
        .then((response) => (response.ok ? response.json() : null))
        .then((data) => {
          if (cancelled) return;
          setBatch(data && data.id && data.task ? (data as ActiveBatch) : null);
        })
        .catch(() => {
          // Not running inside the app; the caller falls back to a free picker.
        });
    };

    read();
    window.addEventListener(ACTIVE_BATCH_CHANGED, read);
    return () => {
      cancelled = true;
      window.removeEventListener(ACTIVE_BATCH_CHANGED, read);
    };
  }, []);

  return batch;
}
