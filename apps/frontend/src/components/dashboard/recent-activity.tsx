"use client";

import { Activity, CheckCircle2, FileEdit, Clock, Send } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { LABELS } from "@/lib/constants";
import type { ReelDraftDTO } from "@factory/shared-types";

interface RecentActivityProps {
  activities: ReelDraftDTO[] | undefined;
  isLoading: boolean;
}

function getActivityIcon(draft: ReelDraftDTO) {
  if (draft.publish_status === "published") {
    return <CheckCircle2 className="h-3.5 w-3.5 text-neon" />;
  }
  if (draft.publish_status === "scheduled") {
    return <Clock className="h-3.5 w-3.5 text-yellow-400" />;
  }
  if (draft.publish_status === "publishing") {
    return <Send className="h-3.5 w-3.5 text-blue-400" />;
  }
  return <FileEdit className="h-3.5 w-3.5 text-muted-foreground" />;
}

function getActivityDescription(draft: ReelDraftDTO): string {
  if (draft.publish_status === "published") {
    return "Đã đăng reel";
  }
  if (draft.publish_status === "scheduled") {
    return "Đã lên lịch";
  }
  if (draft.publish_status === "publishing") {
    return "Đang đăng";
  }
  if (draft.approval_status === "approved") {
    return "Đã duyệt bản nháp";
  }
  if (draft.approval_status === "rejected") {
    return "Đã từ chối";
  }
  return "Đã tạo bản nháp";
}

function getLatestTimestamp(draft: ReelDraftDTO): string {
  return draft.published_at || draft.approved_at || draft.scheduled_at || draft.created_at;
}

function formatActivityTime(iso: string): string {
  const date = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMin = Math.round(diffMs / 60_000);
  const diffHours = Math.round(diffMs / 3_600_000);
  const diffDays = Math.round(diffMs / 86_400_000);

  if (diffMin < 1) return "vừa xong";
  if (diffMin < 60) return `${diffMin} phút trước`;
  if (diffHours < 24) return `${diffHours} giờ trước`;
  if (diffDays < 7) return `${diffDays} ngày trước`;
  return date.toLocaleDateString("vi-VN", { day: "numeric", month: "numeric" });
}

function ActivitySkeleton() {
  return (
    <div className="flex items-center gap-3 py-2.5">
      <Skeleton className="h-7 w-7 shrink-0 rounded-full" />
      <div className="flex-1 space-y-1">
        <Skeleton className="h-3.5 w-3/4" />
        <Skeleton className="h-3 w-1/3" />
      </div>
    </div>
  );
}

export function RecentActivity({ activities, isLoading }: RecentActivityProps) {
  return (
    <Card className="border-border/50 bg-card/80">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base font-semibold">
          <Activity className="h-4 w-4 text-neon" />
          {LABELS.dashboard.recentActivity}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-0 divide-y divide-border/40">
            <ActivitySkeleton />
            <ActivitySkeleton />
            <ActivitySkeleton />
            <ActivitySkeleton />
            <ActivitySkeleton />
          </div>
        ) : !activities || activities.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <Activity className="mb-2 h-8 w-8 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">{LABELS.dashboard.emptyActivity}</p>
          </div>
        ) : (
          <div className="divide-y divide-border/40">
            {activities.map((draft) => (
              <div key={draft.id} className="flex items-center gap-3 py-2.5 first:pt-0 last:pb-0">
                <div className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-muted/60">
                  {getActivityIcon(draft)}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-foreground">
                    <span className="font-medium">{getActivityDescription(draft)}</span>
                    {" \u2014 "}
                    <span className="text-muted-foreground">
                      {draft.title || draft.caption?.slice(0, 30) || "Untitled"}
                    </span>
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {formatActivityTime(getLatestTimestamp(draft))}
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

export default RecentActivity;
