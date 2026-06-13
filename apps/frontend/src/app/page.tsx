"use client";

import { DashboardHeader } from "@/components/dashboard/dashboard-header";
import { FanpageStats } from "@/components/dashboard/fanpage-stats";
import { UpcomingPosts } from "@/components/dashboard/upcoming-posts";
import { QuickActions } from "@/components/dashboard/quick-actions";
import { RecentActivity } from "@/components/dashboard/recent-activity";
import { useDashboard } from "@/hooks/use-dashboard";

export default function DashboardPage() {
  const { data, isLoading } = useDashboard();

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      <DashboardHeader />

      <FanpageStats stats={data?.stats} isLoading={isLoading} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="space-y-6">
          <UpcomingPosts posts={data?.upcomingPosts} isLoading={isLoading} />
        </div>
        <div className="space-y-6">
          <QuickActions />
          <RecentActivity activities={data?.recentActivity} isLoading={isLoading} />
        </div>
      </div>
    </div>
  );
}
