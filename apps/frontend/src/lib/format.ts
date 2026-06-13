// Formatting helpers. Pure, deterministic, no locale-sensitive Date parsing on
// the server vs client mismatch path: we format using UTC-stable arithmetic and
// fall back to toLocaleString only inside client components.

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "--:--";
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => n.toString().padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

const BYTE_UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return "--";
  let n = bytes;
  let i = 0;
  while (n >= 1024 && i < BYTE_UNITS.length - 1) {
    n /= 1024;
    i += 1;
  }
  const precision = n >= 100 || i === 0 ? 0 : 1;
  const unit = BYTE_UNITS[i] ?? "B";
  return `${n.toFixed(precision)} ${unit}`;
}

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, "Z");
}

export function formatRelativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--";
  const diff = (now.getTime() - d.getTime()) / 1000;
  const abs = Math.abs(diff);
  const fmt = (n: number, unit: string) => `${Math.floor(n)}${unit} ${diff >= 0 ? "ago" : "from now"}`;
  if (abs < 60) return fmt(abs, "s");
  if (abs < 3600) return fmt(abs / 60, "m");
  if (abs < 86_400) return fmt(abs / 3600, "h");
  if (abs < 86_400 * 30) return fmt(abs / 86_400, "d");
  if (abs < 86_400 * 365) return fmt(abs / (86_400 * 30), "mo");
  return fmt(abs / (86_400 * 365), "y");
}

export function formatPct(pct: number | null | undefined): string {
  if (pct == null || !Number.isFinite(pct)) return "0%";
  return `${Math.max(0, Math.min(100, Math.round(pct)))}%`;
}
