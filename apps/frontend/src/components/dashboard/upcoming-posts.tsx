"use client";

import { CalendarDays, Clock } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { LABELS } from "@/lib/constants";
import type { ReelDraftDTO } from "@factory/shared-types";

interface UpcomingPostsProps {
  posts: ReelDraftDTO[] | undefined;
  isLoading: boolean;
}

function formatScheduledTime(iso: string): string {
  const target = new Date(iso);
  const now = new Date();
  const diffMs = target.getTime() - now.getTime();
  const diffMin = Math.round(diffMs / 60_000);
  const diffHours = Math.round(diffMs / 3_600_000);

  if (diffMin < 0) return "đã qua";
  if (diffMin < 60) return `trong ${diffMin} phút`;
  if (diffHours < 24) return `trong ${diffHours} giờ`;

  const isNextDay =
    target.getDate() !== now.getDate() ||
    target.getMonth() !== now.getMonth();
  const timeStr = target.toLocaleTimeString("vi-VN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });

  if (isNextDay && diffHours < 48) return `ngày mai lúc ${timeStr}`;
  return `${target.toLocaleDateString("vi-VN", { day: "numeric", month: "numeric" })} lúc ${timeStr}`;
}

function UpcomingPostSkeleton() {
  return (
    <div className="flex items-center gap-3 py-3">
      <Skeleton className="h-2.5 w-2.5 shrink-0 rounded-full" />
      <div className="flex-1 space-y-1.5">
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-3 w-1/2" />
      </div>
    </div>
  );
}

export function UpcomingPosts({ posts, isLoading }: UpcomingPostsProps) {
  return (
    <Card className="border-border/50 bg-card/80">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base font-semibold">
          <CalendarDays className="h-4 w-4 text-neon" />
          {LABELS.dashboard.upcomingPosts}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="divide-y divide-border/40">
            <UpcomingPostSkeleton />
            <UpcomingPostSkeleton />
            <UpcomingPostSkeleton />
          </div>
        ) : !posts || posts.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <Clock className="mb-2 h-8 w-8 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">{LABELS.dashboard.emptyUpcoming}</p>
          </div>
        ) : (
          <div className="divide-y divide-border/40">
            {posts.map((post) => (
              <div key={post.id} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
                <span className="mt-1.5 inline-block h-2.5 w-2.5 shrink-0 rounded-full bg-neon shadow-glow" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-foreground">
                    {post.title || post.caption?.slice(0, 50) || "Untitled reel"}
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {post.scheduled_at ? formatScheduledTime(post.scheduled_at) : "Chưa lên lịch"}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default UpcomingPosts;
