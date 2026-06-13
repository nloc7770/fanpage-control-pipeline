"use client";

import { Film, Upload, CalendarClock } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { LABELS } from "@/lib/constants";
import type { DashboardStats } from "@/hooks/use-dashboard";

interface FanpageStatsProps {
  stats: DashboardStats | undefined;
  isLoading: boolean;
}

function StatCardSkeleton() {
  return (
    <Card className="relative overflow-hidden border-border/50 bg-card/80 p-5">
      <div className="flex items-start justify-between">
        <div className="space-y-2">
          <Skeleton className="h-4 w-16" />
          <Skeleton className="h-8 w-12" />
        </div>
        <Skeleton className="h-9 w-9 rounded-lg" />
      </div>
      <Skeleton className="mt-4 h-2 w-full rounded-full" />
    </Card>
  );
}

export function FanpageStats({ stats, isLoading }: FanpageStatsProps) {
  if (isLoading || !stats) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCardSkeleton />
        <StatCardSkeleton />
        <StatCardSkeleton />
      </div>
    );
  }

  const progressPct = stats.scriptsTotal > 0
    ? Math.round((stats.scriptsCompleted / stats.scriptsTotal) * 100)
    : 0;

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      {/* Scripts progress */}
      <Card className="relative overflow-hidden border-border/50 bg-card/80 p-5 transition-shadow hover:shadow-glow/5">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {LABELS.stats.scripts}
            </p>
            <p className="mt-1 text-2xl font-bold tabular-nums">
              <span className="text-neon">{stats.scriptsCompleted}</span>
              <span className="text-muted-foreground">/{stats.scriptsTotal}</span>
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">{LABELS.stats.scriptsProgress}</p>
          </div>
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-neon/10 text-neon">
            <Film className="h-4 w-4" />
          </div>
        </div>
        <div className="mt-4">
          <Progress value={progressPct} className="h-1.5 bg-secondary" />
        </div>
      </Card>

      {/* Published count */}
      <Card className="relative overflow-hidden border-border/50 bg-card/80 p-5 transition-shadow hover:shadow-glow/5">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {LABELS.stats.published}
            </p>
            <p className="mt-1 text-2xl font-bold tabular-nums text-foreground">
              {stats.publishedCount}
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">reels</p>
          </div>
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-neon/10 text-neon">
            <Upload className="h-4 w-4" />
          </div>
        </div>
        <div className="mt-4 flex items-center gap-1.5">
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-neon shadow-glow" />
          <span className="text-xs text-muted-foreground">Đang hoạt động</span>
        </div>
      </Card>

      {/* Upcoming count */}
      <Card className="relative overflow-hidden border-border/50 bg-card/80 p-5 transition-shadow hover:shadow-glow/5">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
              {LABELS.stats.upcoming}
            </p>
            <p className="mt-1 text-2xl font-bold tabular-nums text-foreground">
              {stats.scheduledCount}
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">đã lên lịch</p>
          </div>
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-neon/10 text-neon">
            <CalendarClock className="h-4 w-4" />
          </div>
        </div>
        <div className="mt-4 flex items-center gap-1.5">
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-neon/60" />
          <span className="text-xs text-muted-foreground">Tự động đăng</span>
        </div>
      </Card>
    </div>
  );
}

export default FanpageStats;
