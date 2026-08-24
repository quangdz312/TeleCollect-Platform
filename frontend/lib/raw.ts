/** Source-neutral metadata contract for teleop and scripted raw episodes. */

import { apiUrl, getToken } from "./api";

export type RawEpisodeSource = "teleop" | "scripted";
export type RawEpisodeQuality = "clean" | "good" | "medium" | "poor";
export type RawEpisodeReviewStatus = "pending" | "approved" | "rejected" | "archived";
export type ArtifactHealth = "healthy" | "warning" | "corrupted";

export interface RawEpisodeCameras {
  front: boolean;
  birdview: boolean;
  wrist: boolean;
}

export interface RawEpisode {
  episode_id: string;
  display_name: string;
  source: RawEpisodeSource;
  task: string;
  created_at: string | null;

  length: number;
  duration_s: number | null;
  control_hz: number | null;
  size_bytes: number | null;

  recorded_success: boolean | null;
  quality: RawEpisodeQuality | null;
  review_status: RawEpisodeReviewStatus;

  operator_id: string | null;
  collection_batch_id: string | null;
  cameras: RawEpisodeCameras;
  artifact_health: ArtifactHealth;
  management_version: number;
}

export interface RawEpisodePage {
  items: RawEpisode[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  summary: RawEpisodeSummary;
  available_tasks: string[];
}

export interface RawEpisodeSummary {
  total: number;
  teleop: number;
  scripted: number;
  successes: number;
  failures: number;
  pending: number;
  approved: number;
  rejected: number;
  archived: number;
}

export interface RawArtifact {
  name: string;
  kind: "metadata" | "table" | "video" | "dataset";
  exists: boolean;
  size_bytes: number | null;
  camera: "front" | "birdview" | "wrist" | null;
}

export interface RawEpisodeDetail extends RawEpisode {
  artifacts: RawArtifact[];
  audit: RawEpisodeAudit[];
}

export interface RawEpisodeAudit {
  id: string;
  action: string;
  changes: Record<string, unknown>;
  actor_name: string;
  created_at: string;
}

export interface RawSignalSeries {
  labels: string[];
  values: number[][];
}

export interface RawSignals {
  episode_id: string;
  source: RawEpisodeSource;
  start: number;
  end: number;
  total_frames: number;
  sampled_frames: number[];
  control_hz: number;
  video_stride: number;
  time_basis: "recorded" | "scripted_playback";
  t: number[];
  available_fields: string[];
  signals: Record<string, RawSignalSeries>;
}

export interface RawEpisodeFilters {
  source?: RawEpisodeSource;
  task?: string;
  quality?: RawEpisodeQuality;
  outcome?: "success" | "failure";
  review_status?: RawEpisodeReviewStatus;
  search?: string;
  page?: number;
  page_size?: number;
}

export class RawApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "RawApiError";
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(apiUrl(`/raw${path}`), { ...init, headers });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    throw new RawApiError(
      (body as { detail?: string } | null)?.detail ?? `${response.status} ${response.statusText}`,
      response.status,
    );
  }
  return body as T;
}

export const rawApi = {
  episodes: (filters: RawEpisodeFilters = {}) => {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value !== undefined && value !== "") query.set(key, String(value));
    }
    return request<RawEpisodePage>(`/episodes?${query}`);
  },
  detail: (episodeId: string) =>
    request<RawEpisodeDetail>(`/episodes/${encodeURIComponent(episodeId)}`),
  signals: (episodeId: string, fields = "action,ee_pose,qpos,qvel,reward", maxPoints = 600) => {
    const query = new URLSearchParams({ fields, max_points: String(maxPoints) });
    return request<RawSignals>(`/episodes/${encodeURIComponent(episodeId)}/signals?${query}`);
  },
  videoUrl: (episodeId: string, camera: "front" | "birdview" | "wrist" | "composite") => {
    const token = getToken();
    const query = new URLSearchParams();
    if (token) query.set("token", token);
    const suffix = query.size ? `?${query}` : "";
    return apiUrl(`/raw/episodes/${encodeURIComponent(episodeId)}/video/${camera}${suffix}`);
  },
  prepareVideo: (episodeId: string) =>
    request<{ status: "ready" | "rendering" }>(
      `/episodes/${encodeURIComponent(episodeId)}/video/prepare`,
      { method: "POST" },
    ),
  archive: (episodeId: string, archived: boolean, expectedVersion: number) =>
    request<RawEpisodeDetail>(`/episodes/${encodeURIComponent(episodeId)}/archive`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ archived, expected_version: expectedVersion }),
    }),
};
