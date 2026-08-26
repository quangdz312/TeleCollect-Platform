import type { RawEpisode } from "./raw";

export const CONVERT_SELECTION_KEY = "telecollect-convert-selection-v1";

export function readConvertSelection(): RawEpisode[] {
  if (typeof window === "undefined") return [];
  try {
    const value = JSON.parse(window.sessionStorage.getItem(CONVERT_SELECTION_KEY) ?? "[]");
    return Array.isArray(value) ? value as RawEpisode[] : [];
  } catch {
    return [];
  }
}

export function writeConvertSelection(episodes: RawEpisode[]) {
  window.sessionStorage.setItem(CONVERT_SELECTION_KEY, JSON.stringify(episodes));
}

export function clearConvertSelection() {
  window.sessionStorage.removeItem(CONVERT_SELECTION_KEY);
}
