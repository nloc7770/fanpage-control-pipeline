"use client";

import { useMemo } from "react";
import { BookOpen, Film } from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { useScripts } from "@/hooks/use-scripts";
import { ScriptCard } from "@/components/scripts/script-card";
import type { ScriptStatus } from "@/hooks/use-scripts";

type FilterTab = "all" | ScriptStatus;

export default function ScriptsPage() {
  const { data, isLoading, error } = useScripts();

  const scripts = useMemo(() => data?.scripts ?? [], [data]);

  const filmedCount = useMemo(
    () => scripts.filter((s) => s.status === "filmed" || s.status === "published").length,
    [scripts],
  );

  const filterGroups: Record<FilterTab, typeof scripts> = useMemo(
    () => ({
      all: scripts,
      unfilmed: scripts.filter((s) => s.status === "unfilmed"),
      filmed: scripts.filter((s) => s.status === "filmed"),
      published: scripts.filter((s) => s.status === "published"),
    }),
    [scripts],
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-neon/10">
            <BookOpen className="h-5 w-5 text-neon" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-foreground">Kịch bản video</h1>
            <p className="text-sm text-muted-foreground">
              Quản lý kịch bản quay phim
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <Film className="h-4 w-4 text-neon" />
          <span className="text-muted-foreground">
            <span className="font-semibold text-foreground">{filmedCount}</span>
            /{scripts.length} đã quay
          </span>
        </div>
      </div>

      {/* Filter Tabs */}
      <Tabs defaultValue="all" className="w-full">
        <TabsList className="bg-muted/50">
          <TabsTrigger value="all">
            Tất cả ({scripts.length})
          </TabsTrigger>
          <TabsTrigger value="unfilmed">
            Chưa quay ({filterGroups.unfilmed.length})
          </TabsTrigger>
          <TabsTrigger value="filmed">
            Đã quay ({filterGroups.filmed.length})
          </TabsTrigger>
          <TabsTrigger value="published">
            Đã đăng ({filterGroups.published.length})
          </TabsTrigger>
        </TabsList>

        {isLoading ? (
          <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-40 rounded-lg" />
            ))}
          </div>
        ) : error ? (
          <div className="mt-6 text-center py-12">
            <p className="text-sm text-destructive">Không thể tải kịch bản. Vui lòng thử lại.</p>
          </div>
        ) : (
          <>
            {(["all", "unfilmed", "filmed", "published"] as FilterTab[]).map((tab) => (
              <TabsContent key={tab} value={tab}>
                {filterGroups[tab].length === 0 ? (
                  <div className="text-center py-12">
                    <BookOpen className="mx-auto h-10 w-10 text-muted-foreground/40 mb-3" />
                    <p className="text-sm text-muted-foreground">Không có kịch bản nào</p>
                  </div>
                ) : (
                  <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                    {filterGroups[tab].map((script) => (
                      <ScriptCard key={script.slug} script={script} />
                    ))}
                  </div>
                )}
              </TabsContent>
            ))}
          </>
        )}
      </Tabs>
    </div>
  );
}
