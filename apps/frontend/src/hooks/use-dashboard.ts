"use client";

import { useQuery } from "@tanstack/react-query";
import { listReelDrafts } from "@/lib/api-fb";
import type { ReelDraftDTO } from "@factory/shared-types";

export interface DashboardStats {
  scriptsTotal: number;
  scriptsCompleted: number;
  publishedCount: number;
  scheduledCount: number;
}

export interface DashboardData {
  stats: DashboardStats;
  upcomingPosts: ReelDraftDTO[];
  recentActivity: ReelDraftDTO[];
}

function getLatestTimestamp(draft: ReelDraftDTO): number {
  const dates = [
    draft.published_at,
    draft.approved_at,
    draft.scheduled_at,
    draft.created_at,
  ].filter(Boolean) as string[];

  if (dates.length === 0) return 0;
  return Math.max(...dates.map((d) => new Date(d).getTime()));
}

async function fetchDashboardData(): Promise<DashboardData> {
  const [allDrafts, scheduledDrafts, publishedDrafts] = await Promise.all([
    listReelDrafts({ limit: 50 }),
    listReelDrafts({ publish_status: "scheduled", limit: 10 }),
    listReelDrafts({ publish_status: "published", limit: 10 }),
  ]);

  const scriptsTotal = 10;
  const scriptsCompleted = publishedDrafts.drafts.length;
  const publishedCount = publishedDrafts.total;
  const scheduledCount = scheduledDrafts.total;

  // Upcoming: next 3 scheduled sorted by scheduled_at
  const upcoming = scheduledDrafts.drafts
    .filter((d) => d.scheduled_at)
    .sort((a, b) => new Date(a.scheduled_at!).getTime() - new Date(b.scheduled_at!).getTime())
    .slice(0, 3);

  // Recent activity: last 5 drafts sorted by latest timestamp descending
  const recent = [...allDrafts.drafts]
    .sort((a, b) => getLatestTimestamp(b) - getLatestTimestamp(a))
    .slice(0, 5);

  return {
    stats: {
      scriptsTotal,
      scriptsCompleted: Math.min(scriptsCompleted, scriptsTotal),
      publishedCount,
      scheduledCount,
    },
    upcomingPosts: upcoming,
    recentActivity: recent,
  };
}

export function useDashboard() {
  return useQuery<DashboardData>({
    queryKey: ["dashboard-stats"],
    queryFn: fetchDashboardData,
    refetchInterval: 30_000,
    staleTime: 10_000,
    refetchOnWindowFocus: true,
  });
}
