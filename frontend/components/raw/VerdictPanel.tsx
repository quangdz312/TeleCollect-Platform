"use client";

/**
 * Review verdict, sitting beside the camera playback.
 *
 * Reviewing used to mean leaving this page for a separate queue, so the video
 * was watched in one place and the decision taken in another. Keeping them
 * side by side matches the desktop app: watch, then decide without navigating.
 *
 * Scripted episodes only. Their verdicts live in the labeling workspace, while
 * teleop captures already carry the simulator's own success flag and follow the
 * separate demo review flow.
 */

import { useState } from "react";
import { Alert, Badge, Button, Card, TextArea } from "@/components/ui";
import { labeling, type Decision } from "@/lib/labeling";
import type { RawEpisodeDetail } from "@/lib/raw";

export function VerdictPanel({
  episode,
  reviewer,
  onDecided,
}: {
  episode: RawEpisodeDetail;
  reviewer: string;
  onDecided: () => Promise<void> | void;
}) {
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState<Decision | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Decision | null>(null);

  async function decide(decision: Decision) {
    setSaving(decision);
    setError(null);
    try {
      await labeling.submitLabel({
        episode_id: episode.episode_id,
        decision,
        reasons: [],
        note: note.trim(),
        reviewer,
        blind: false,
      });
      setDone(decision);
      setNote("");
      await onDecided();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Could not save the verdict");
    } finally {
      setSaving(null);
    }
  }

  if (episode.source !== "scripted") {
    return (
      <Card title="Verdict">
        <p className="text-sm text-ink-400">
          Teleop captures carry the simulator&apos;s own outcome and follow the demo review
          flow, so they are not decided here.
        </p>
      </Card>
    );
  }

  return (
    <Card title="Verdict" subtitle="Watch the episode and decide without leaving the page.">
      <div className="space-y-4">
        <div className="flex flex-wrap gap-2">
          <Badge tone="info">{episode.source}</Badge>
          <Badge>{episode.task}</Badge>
          <Badge>{episode.length.toLocaleString()} frames</Badge>
        </div>

        {done ? (
          <Alert tone={done === "approved" ? "ok" : "bad"}>
            Recorded as {done === "approved" ? "accepted" : "rejected"}.
          </Alert>
        ) : null}
        {error ? <Alert tone="bad">{error}</Alert> : null}

        <label className="block">
          <span className="mb-1 block text-xs font-medium text-ink-300">Reviewer notes</span>
          <TextArea
            rows={3}
            value={note}
            placeholder="Reason for rejecting, or anything worth flagging"
            onChange={(event) => setNote(event.target.value)}
          />
        </label>

        <div className="grid grid-cols-2 gap-2">
          <Button
            variant="success"
            disabled={saving !== null}
            onClick={() => void decide("approved")}
          >
            {saving === "approved" ? "Saving…" : "Accept"}
          </Button>
          <Button
            variant="danger"
            disabled={saving !== null}
            onClick={() => void decide("rejected")}
          >
            {saving === "rejected" ? "Saving…" : "Reject"}
          </Button>
        </div>
      </div>
    </Card>
  );
}
