/** Source-neutral metadata contract for teleop and scripted raw episodes. */

import { apiUrl, getToken } from "./api";
import { COLLECTION_ENABLED } from "./features";

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
  available_batches: string[];
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
  by_task: Record<string, number>;
  by_quality: Record<string, number>;
  by_batch: Record<string, number>;
  by_day: Record<string, { teleop: number; scripted: number }>;
  undated: number;
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
  collection_batch_id?: string;
  search?: string;
  page?: number;
  page_size?: number;
}

/**
 * A collection batch.
 *
 * `named: false` means the batch exists only as a string in episode
 * provenance and nobody has created a record for it yet — the UI uses the flag
 * to offer naming it instead of showing a raw identifier.
 */
export interface CollectionBatch {
  id: string;
  name: string;
  task_name: string | null;
  description: string;
  archived: boolean;
  created_at: string | null;
  named: boolean;
  episodes: number;
  teleop: number;
  scripted: number;
  pending: number;
  approved: number;
  rejected: number;
}

export interface CollectionBatchImport {
  batch: CollectionBatch;
  episodes: number;
  videos: number;
  sources: string[];
  skipped: { episode: string; reason: string }[];
}

export interface CollectionBatchInput {
  id: string;
  name: string;
  task_name?: string | null;
  description?: string;
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
  /**
   * Upload a zipped app batch folder.
   *
   * `fetch` cannot report how far a large upload has got, and a batch carries
   * every rendered video, so this one call uses XHR to drive a progress bar.
   */
  importBatch: (
    // Bo trong thi may chu lay ten tu batch.json trong file zip, trung thi
    // them so dem. Truyen vao khi muon gop them du lieu vao mot dot thu co san.
    batchId: string | null,
    params: {
      archive: File;
      name?: string;
      overwrite?: boolean;
      onProgress?: (percent: number) => void;
    },
  ): Promise<CollectionBatchImport> =>
    new Promise((resolve, reject) => {
      const form = new FormData();
      form.set("archive", params.archive);
      if (params.name) form.set("name", params.name);
      if (params.overwrite) form.set("overwrite", "true");

      const xhr = new XMLHttpRequest();
      xhr.open(
        "POST",
        batchId
          ? apiUrl(`/raw/batches/${encodeURIComponent(batchId)}/import`)
          : apiUrl("/raw/batches/import"),
      );
      const token = getToken();
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          params.onProgress?.(Math.round((event.loaded / event.total) * 100));
        }
      };
      xhr.onerror = () => reject(new RawApiError("Could not reach the server", 0));
      xhr.onload = () => {
        let payload: unknown = null;
        try {
          payload = JSON.parse(xhr.responseText);
        } catch {
          payload = null;
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(payload as CollectionBatchImport);
          return;
        }
        reject(
          new RawApiError(
            (payload as { detail?: string } | null)?.detail ??
              `${xhr.status} ${xhr.statusText}`,
            xhr.status,
          ),
        );
      };
      xhr.send(form);
    }),
  batches: (includeArchived = false) =>
    request<CollectionBatch[]>(
      `/batches${includeArchived ? "?include_archived=true" : ""}`,
    ),
  /**
   * Dựng một đợt thu mới.
   *
   * Trong app phải đi qua `/local`, không phải `/raw`. App giữ hai kho đợt thu
   * riêng: bảng trong cơ sở dữ liệu, và `catalog` cùng thư mục thật trên đĩa.
   * `/raw` chỉ ghi vào kho thứ nhất, nên đợt thu tạo từ đây hiện trên trang
   * Review mà không có trong ô chọn ở màn hình thu dữ liệu — ô đó đọc đĩa — và
   * cũng không có thư mục để ghi tập vào. `/local` ghi cả hai.
   *
   * Trên web dựng sẵn thì đường `/local` không tồn tại, và ở đó chỉ có một kho
   * nên `/raw` là đúng.
   */
  createBatch: async (input: CollectionBatchInput): Promise<CollectionBatch> => {
    if (!COLLECTION_ENABLED) {
      return request<CollectionBatch>("/batches", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      });
    }
    const headers = new Headers({ "Content-Type": "application/json" });
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(apiUrl("/local/batches"), {
      method: "POST",
      headers,
      // App tự sinh mã và gọi tên trường là `task`; `id` bên này bị bỏ qua.
      body: JSON.stringify({
        name: input.name,
        task: input.task_name ?? "",
        description: input.description ?? "",
      }),
    });
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      throw new RawApiError(
        (body as { detail?: string } | null)?.detail ?? `${response.status} ${response.statusText}`,
        response.status,
      );
    }
    // App trả bản ghi của riêng nó (`task`, `folder`), không phải hình dạng của
    // `/raw`. Bên gọi nạp lại danh sách sau khi tạo nên không đọc giá trị này;
    // dựng lại đúng vài trường chung thay vì ép kiểu một thứ khác hẳn.
    const created = (body ?? {}) as { id?: string; name?: string; task?: string };
    return {
      ...(body as object),
      id: created.id ?? "",
      name: created.name ?? input.name,
      task_name: created.task ?? input.task_name ?? null,
    } as CollectionBatch;
  },
  updateBatch: (
    batchId: string,
    changes: Partial<Omit<CollectionBatch, "id" | "named" | "created_at">>,
  ) =>
    request<CollectionBatch>(`/batches/${encodeURIComponent(batchId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes),
    }),
  // `purgeEpisodes` also deletes the teleop captures and their files; without
  // it only the batch's name and description go, and the episodes stay behind
  // as an unnamed batch.
  deleteBatch: (batchId: string, purgeEpisodes = false) =>
    request<null>(
      `/batches/${encodeURIComponent(batchId)}${purgeEpisodes ? "?purge_episodes=true" : ""}`,
      { method: "DELETE" },
    ),

  /**
   * Phiên đăng nhập máy chủ mà app đang giữ, `null` khi chưa đăng nhập.
   *
   * Nút Push chỉ có nghĩa khi đã có phiên: chưa đăng nhập thì cú đẩy nào cũng
   * hỏng, và một nút luôn hỏng thì tệ hơn là không có nút.
   */
  syncSession: async (): Promise<{ server: string; username: string; role: string } | null> => {
    const headers = new Headers();
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(apiUrl("/local/sync"), { headers });
    if (!response.ok) return null;
    return (await response.json().catch(() => null)) as
      | { server: string; username: string; role: string }
      | null;
  },

  /**
   * Đẩy một đợt thu từ máy này lên máy chủ dùng chung. Chỉ app mới gọi được.
   *
   * Không đi qua `request`: nó gắn sẵn tiền tố `/raw`, còn đường này nằm ở
   * `/local` — API mà chỉ backend của app mới gắn vào. Trên web đã dựng, gọi
   * nó sẽ ra 404, nên nút gọi nó phải ẩn theo cờ chứ không chỉ báo lỗi.
   *
   * Máy chủ nào, tài khoản nào là do phiên đăng nhập lưu trong app quyết định,
   * không phải trang này — nên ở đây không có tham số nào cho chúng.
   */
  pushBatch: async (batchId: string): Promise<{ episodes: number; videos: number }> => {
    const headers = new Headers();
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(
      apiUrl(`/local/batches/${encodeURIComponent(batchId)}/sync`),
      { method: "POST", headers },
    );
    const body = await response.json().catch(() => null);
    if (!response.ok) {
      throw new RawApiError(
        (body as { detail?: string } | null)?.detail ?? `${response.status} ${response.statusText}`,
        response.status,
      );
    }
    return body as { episodes: number; videos: number };
  },
};
