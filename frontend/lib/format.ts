export function bytes(value: number): string {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export function duration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds % 60)}s`;
}

export function percent(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function timeAgo(iso: string): string {
  const then = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`).getTime();
  const delta = (Date.now() - then) / 1000;
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

export function frameTime(frame: number, fps: number): string {
  const seconds = frame / fps;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${(seconds % 60).toFixed(2).padStart(5, "0")}`;
}

/**
 * Tên checkpoint đủ ngắn để đọc trong một cột hẹp.
 *
 * Tên đầy đủ mà robomimic sinh ra là
 * `lift2/20260829172036/models/model_epoch_242_best_validation_0.002359669259749353.pth`:
 * thư mục đứng trước giống hệt nhau ở mọi dòng, còn đuôi validation loss thì
 * bảng đã có riêng một cột và một nhãn "best validation". Cắt cả hai chỉ còn
 * `model_epoch_242.pth`; đường dẫn đầy đủ vẫn nằm trong tooltip của ô.
 */
export function checkpointName(path: string): string {
  const file = path.split("/").pop() ?? path;
  return file.replace(/_best_validation_[\d.eE+-]+(?=\.pth$)/, "");
}
