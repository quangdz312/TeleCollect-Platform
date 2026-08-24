"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Badge, Button, Select } from "@/components/ui";
import { rawApi, type RawEpisodeDetail } from "@/lib/raw";

type Camera = "front" | "birdview" | "wrist" | "composite";

function clock(seconds: number) {
  if (!Number.isFinite(seconds)) return "0:00.00";
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${(seconds % 60).toFixed(2).padStart(5, "0")}`;
}

export function SynchronizedPlayback({
  episode,
  onTimeChange,
  seekRequest,
}: {
  episode: RawEpisodeDetail;
  onTimeChange?: (time: number) => void;
  seekRequest?: { time: number; nonce: number } | null;
}) {
  const scriptedVideo = episode.artifacts.find((item) => item.kind === "video");
  const [scriptedReady, setScriptedReady] = useState(scriptedVideo?.exists ?? false);
  const [preparing, setPreparing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [failed, setFailed] = useState<Set<Camera>>(new Set());
  const [playing, setPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [mediaDuration, setMediaDuration] = useState(episode.duration_s ?? 0);
  const [rate, setRate] = useState(1);
  const videoRefs = useRef<Partial<Record<Camera, HTMLVideoElement>>>({});

  const streams = useMemo<Array<{ camera: Camera; label: string }>>(() => {
    if (episode.source === "scripted") {
      return scriptedReady
        ? [{ camera: "composite" as const, label: "Three-view composite" }]
        : [];
    }
    const result: Array<{ camera: Camera; label: string }> = [];
    if (episode.cameras.front) result.push({ camera: "front", label: "Front" });
    if (episode.cameras.birdview) result.push({ camera: "birdview", label: "Birdview" });
    if (episode.cameras.wrist) result.push({ camera: "wrist", label: "Wrist / eye-in-hand" });
    return result;
  }, [episode, scriptedReady]);

  const videos = () => streams
    .map((stream) => videoRefs.current[stream.camera])
    .filter((video): video is HTMLVideoElement => Boolean(video));

  const seek = (seconds: number) => {
    const bounded = Math.max(0, Math.min(seconds, mediaDuration || seconds));
    for (const video of videos()) video.currentTime = bounded;
    setCurrentTime(bounded);
    onTimeChange?.(bounded);
  };

  useEffect(() => {
    if (!seekRequest) return;
    const bounded = Math.max(0, Math.min(seekRequest.time, mediaDuration || seekRequest.time));
    for (const video of Object.values(videoRefs.current)) {
      if (video) video.currentTime = bounded;
    }
    setCurrentTime(bounded);
    onTimeChange?.(bounded);
  }, [seekRequest, mediaDuration, onTimeChange]);

  const togglePlayback = async () => {
    const elements = videos();
    if (!elements.length) return;
    if (playing) {
      elements.forEach((video) => video.pause());
      setPlaying(false);
      return;
    }
    const masterTime = elements[0].currentTime;
    elements.forEach((video) => {
      video.currentTime = masterTime;
      video.playbackRate = rate;
    });
    await Promise.allSettled(elements.map((video) => video.play()));
    setPlaying(true);
  };

  const changeRate = (next: number) => {
    setRate(next);
    videos().forEach((video) => { video.playbackRate = next; });
  };

  const prepareScripted = async () => {
    setPreparing(true);
    setMessage(null);
    try {
      const result = await rawApi.prepareVideo(episode.episode_id);
      if (result.status === "ready") {
        setScriptedReady(true);
        setMessage("Playback is ready.");
      } else {
        setMessage("Rendering started. Use Check again in a few seconds.");
      }
    } catch (problem) {
      setMessage(problem instanceof Error ? problem.message : "Could not prepare video");
    } finally {
      setPreparing(false);
    }
  };

  if (episode.source === "scripted" && !scriptedReady) {
    return (
      <div className="rounded-xl border border-dashed border-ink-700 px-5 py-10 text-center">
        <p className="text-sm text-ink-400">The scripted three-view playback has not been rendered yet.</p>
        <Button className="mt-4" variant="primary" disabled={preparing} onClick={() => void prepareScripted()}>
          {preparing ? "Checking…" : message ? "Check again" : "Prepare playback"}
        </Button>
        {message && <p className="mt-3 text-xs text-ink-400">{message}</p>}
      </div>
    );
  }

  if (!streams.length) return <Alert tone="info">No playable camera stream is available.</Alert>;

  const master = streams[0].camera;
  const frameStep = 1 / (episode.control_hz || 30);

  return (
    <div className="space-y-3">
      <div className={episode.source === "scripted" ? "grid" : "grid gap-3 lg:grid-cols-3"}>
        {streams.map((stream) => (
          <div key={stream.camera} className="overflow-hidden rounded-xl border border-tech-border bg-tech-bg">
            <div className="flex items-center justify-between border-b border-tech-border bg-tech-bg-alt px-3 py-2">
              <div className="flex items-center gap-2 text-xs font-semibold text-tech-text">
                {stream.label}
                {stream.camera === master && episode.source === "teleop" && <Badge tone="info">Master</Badge>}
              </div>
              <button
                type="button"
                className="text-[11px] text-tech-muted hover:text-tech-text"
                onClick={() => void videoRefs.current[stream.camera]?.requestFullscreen()}
              >
                Fullscreen
              </button>
            </div>
            <div className={episode.source === "scripted" ? "aspect-[3/2]" : "aspect-square"}>
              <video
                ref={(node) => {
                  if (node) videoRefs.current[stream.camera] = node;
                  else delete videoRefs.current[stream.camera];
                }}
                src={rawApi.videoUrl(episode.episode_id, stream.camera)}
                preload="metadata"
                muted
                playsInline
                className="h-full w-full bg-black object-contain"
                onLoadedMetadata={(event) => {
                  const loadedDuration = event.currentTarget.duration;
                  if (Number.isFinite(loadedDuration)) {
                    setMediaDuration((value) => Math.max(value, loadedDuration));
                  }
                  event.currentTarget.playbackRate = rate;
                }}
                onTimeUpdate={(event) => {
                  if (stream.camera !== master) return;
                  const time = event.currentTarget.currentTime;
                  setCurrentTime(time);
                  onTimeChange?.(time);
                  for (const follower of videos().slice(1)) {
                    if (Math.abs(follower.currentTime - time) > 0.12) follower.currentTime = time;
                  }
                }}
                onPause={() => {
                  if (stream.camera === master) setPlaying(false);
                }}
                onEnded={() => setPlaying(false)}
                onError={() => setFailed((current) => new Set(current).add(stream.camera))}
              />
            </div>
            {failed.has(stream.camera) && (
              <div className="border-t border-tech-border px-3 py-2 text-xs text-bad-400">Stream could not be loaded.</div>
            )}
          </div>
        ))}
      </div>

      <div className="rounded-xl border border-ink-700 bg-ink-850 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" onClick={() => void togglePlayback()}>{playing ? "Pause" : "Play"}</Button>
          <Button variant="subtle" onClick={() => { videos().forEach((video) => video.pause()); setPlaying(false); seek(currentTime - frameStep); }}>−1 frame</Button>
          <Button variant="subtle" onClick={() => { videos().forEach((video) => video.pause()); setPlaying(false); seek(currentTime + frameStep); }}>+1 frame</Button>
          <span className="min-w-[110px] font-mono text-xs text-ink-300">{clock(currentTime)} / {clock(mediaDuration)}</span>
          <Select
            aria-label="Playback speed"
            className="ml-auto w-28"
            value={rate}
            onChange={(event) => changeRate(Number(event.target.value))}
          >
            <option value={0.25}>0.25×</option>
            <option value={0.5}>0.5×</option>
            <option value={1}>1×</option>
            <option value={1.5}>1.5×</option>
            <option value={2}>2×</option>
          </Select>
        </div>
        <input
          aria-label="Playback timeline"
          type="range"
          min={0}
          max={Math.max(mediaDuration, 0.01)}
          step={0.01}
          value={Math.min(currentTime, mediaDuration || currentTime)}
          onChange={(event) => seek(Number(event.target.value))}
          className="mt-3 w-full"
        />
        <p className="mt-1 text-[11px] text-ink-400">
          {episode.source === "teleop"
            ? "Front is the master clock; Birdview and Wrist are corrected when drift exceeds 120 ms."
            : "Scripted playback is a pre-rendered composite containing the main, birdview and wrist panes."}
        </p>
      </div>
    </div>
  );
}
