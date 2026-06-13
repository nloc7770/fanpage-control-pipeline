import { cn } from "@/lib/cn";

type PublishStatus = "draft" | "approved" | "scheduled" | "publishing" | "published" | "failed" | string;

interface StatusConfig {
  label: string;
  classes: string;
}

const STATUS_CONFIG: Record<string, StatusConfig> = {
  draft: {
    label: "Nháp",
    classes: "bg-muted/60 text-muted-foreground border-border/60",
  },
  approved: {
    label: "Đã duyệt",
    classes: "bg-sky-500/10 text-sky-400 border-sky-500/30",
  },
  scheduled: {
    label: "Đã lên lịch",
    classes: "bg-sky-500/10 text-sky-400 border-sky-500/30",
  },
  publishing: {
    label: "Đang đăng",
    classes: "bg-amber-500/10 text-amber-400 border-amber-500/30 animate-pulse",
  },
  published: {
    label: "Đã đăng",
    classes: "bg-neon/10 text-neon border-neon/30",
  },
  failed: {
    label: "Lỗi",
    classes: "bg-rose-500/10 text-rose-400 border-rose-500/30",
  },
};

const DEFAULT_CONFIG: StatusConfig = {
  label: "Nháp",
  classes: "bg-muted/60 text-muted-foreground border-border/60",
};

interface PublishStatusBadgeProps {
  status: PublishStatus;
  className?: string;
}

export function PublishStatusBadge({ status, className }: PublishStatusBadgeProps) {
  const config = STATUS_CONFIG[status] ?? DEFAULT_CONFIG;

  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold tracking-wide",
        config.classes,
        className,
      )}
    >
      {status === "publishing" && (
        <span className="mr-1.5 relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75 animate-ping" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-amber-400" />
        </span>
      )}
      {status === "published" && (
        <span className="mr-1.5 relative flex h-1.5 w-1.5">
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-neon shadow-[0_0_4px_rgba(57,255,20,0.6)]" />
        </span>
      )}
      {config.label}
    </span>
  );
}

export default PublishStatusBadge;
