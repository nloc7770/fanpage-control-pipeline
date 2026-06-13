import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/cn";
import type { ScriptStatus } from "@/hooks/use-scripts";

const statusConfig: Record<ScriptStatus, { label: string; className: string }> = {
  unfilmed: {
    label: "Chưa quay",
    className: "border-transparent bg-muted text-muted-foreground",
  },
  filmed: {
    label: "Đã quay",
    className: "border-transparent bg-amber-500/15 text-amber-400",
  },
  published: {
    label: "Đã đăng",
    className: "border-transparent bg-neon/15 text-neon",
  },
};

interface ScriptStatusBadgeProps {
  status: ScriptStatus;
  className?: string;
}

export function ScriptStatusBadge({ status, className }: ScriptStatusBadgeProps) {
  const config = statusConfig[status];
  return (
    <Badge className={cn(config.className, className)}>
      {config.label}
    </Badge>
  );
}

export default ScriptStatusBadge;
