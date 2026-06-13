"use client";

import { Suspense, useCallback, useMemo } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Plus, Inbox } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ReelDraftCard } from "@/components/content/reel-draft-card";
import { useReelDrafts } from "@/hooks/use-reel-drafts";
import { cn } from "@/lib/cn";

const LIMIT = 30;

type StatusFilter = "" | "draft" | "approved" | "scheduled" | "published" | "failed";

const STATUS_TABS: { value: StatusFilter; label: string }[] = [
  { value: "", label: "Tất cả" },
  { value: "draft", label: "Nháp" },
  { value: "approved", label: "Đã duyệt" },
  { value: "scheduled", label: "Đã lên lịch" },
  { value: "published", label: "Đã đăng" },
  { value: "failed", label: "Lỗi" },
];

function statusToFilters(status: StatusFilter) {
  switch (status) {
    case "draft":
      return { approval_status: "pending", publish_status: "draft" };
    case "approved":
      return { approval_status: "approved", publish_status: "draft" };
    case "scheduled":
      return { publish_status: "scheduled" };
    case "published":
      return { publish_status: "published" };
    case "failed":
      return { publish_status: "failed" };
    default:
      return {};
  }
}

function ContentPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const statusFilter = (searchParams.get("status") ?? "") as StatusFilter;
  const offset = parseInt(searchParams.get("offset") ?? "0", 10);

  const filters = useMemo(
    () => ({
      ...statusToFilters(statusFilter),
      limit: LIMIT,
      offset,
    }),
    [statusFilter, offset],
  );

  const query = useReelDrafts(filters);
  const drafts = query.data?.drafts ?? [];
  const total = query.data?.total ?? 0;

  const setStatus = useCallback(
    (value: string) => {
      const params = new URLSearchParams();
      if (value) params.set("status", value);
      router.replace(`/content${params.toString() ? `?${params.toString()}` : ""}`, {
        scroll: false,
      });
    },
    [router],
  );

  const setOffset = useCallback(
    (newOffset: number) => {
      const params = new URLSearchParams(searchParams.toString());
      if (newOffset > 0) params.set("offset", String(newOffset));
      else params.delete("offset");
      router.replace(`/content?${params.toString()}`, { scroll: false });
    },
    [router, searchParams],
  );

  const hasPrev = offset > 0;
  const hasNext = offset + LIMIT < total;

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-1 py-1 md:px-2">
      {/* Header */}
      <header className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">Nội dung</h1>
          {!query.isLoading && (
            <span className="inline-flex items-center rounded-full bg-neon/10 border border-neon/20 px-2.5 py-0.5 text-xs font-medium text-neon tabular-nums">
              {total}
            </span>
          )}
        </div>
        <Button asChild size="sm" className="gap-1.5 bg-neon text-black hover:bg-neon/90 font-medium">
          <Link href="/scripts">
            <Plus className="h-4 w-4" />
            Tạo mới
          </Link>
        </Button>
      </header>

      {/* Status tabs */}
      <Tabs value={statusFilter} onValueChange={setStatus}>
        <TabsList className="bg-card/60 border border-border/60 h-auto flex-wrap">
          {STATUS_TABS.map((tab) => (
            <TabsTrigger
              key={tab.value}
              value={tab.value}
              className={cn(
                "text-xs data-[state=active]:bg-neon/15 data-[state=active]:text-neon data-[state=active]:shadow-none",
              )}
            >
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {/* Content grid */}
      {query.isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-72 w-full rounded-lg" />
          ))}
        </div>
      ) : query.isError ? (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/5 p-4 text-sm text-rose-300">
          Không thể tải nội dung. Vui lòng thử lại.
        </div>
      ) : drafts.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border/60 bg-card/30 px-6 py-14 text-center">
          <span className="flex h-10 w-10 items-center justify-center rounded-full bg-neon/10 text-neon">
            <Inbox className="h-5 w-5" aria-hidden />
          </span>
          <div className="space-y-1">
            <div className="text-sm font-semibold text-foreground">Chưa có nội dung</div>
            <p className="max-w-sm text-xs text-muted-foreground">
              {statusFilter
                ? "Không tìm thấy nội dung phù hợp với bộ lọc."
                : "Nội dung sẽ xuất hiện khi bạn tạo kịch bản mới."}
            </p>
          </div>
          <Button asChild variant="outline" size="sm" className="mt-2">
            <Link href="/scripts">Tạo kịch bản</Link>
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {drafts.map((draft) => (
            <ReelDraftCard key={draft.id} draft={draft} />
          ))}
        </div>
      )}

      {/* Pagination */}
      {total > LIMIT && (
        <div className="flex items-center justify-center gap-3 pt-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!hasPrev}
            onClick={() => setOffset(Math.max(0, offset - LIMIT))}
          >
            Trước
          </Button>
          <span className="text-xs text-muted-foreground tabular-nums">
            Trang {Math.floor(offset / LIMIT) + 1} / {Math.ceil(total / LIMIT)}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={!hasNext}
            onClick={() => setOffset(offset + LIMIT)}
          >
            Sau
          </Button>
        </div>
      )}
    </div>
  );
}

export default function ContentPage() {
  return (
    <Suspense
      fallback={
        <div className="mx-auto max-w-7xl space-y-6 px-1 py-1 md:px-2">
          <Skeleton className="h-8 w-40" />
          <Skeleton className="h-10 w-full max-w-lg" />
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-72 w-full rounded-lg" />
            ))}
          </div>
        </div>
      }
    >
      <ContentPageInner />
    </Suspense>
  );
}
