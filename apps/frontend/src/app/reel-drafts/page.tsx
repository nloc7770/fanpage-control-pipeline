"use client";

import { Suspense, useCallback } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Inbox } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useReelDrafts } from "@/hooks/use-reel-drafts";
import { useFacebookPages } from "@/hooks/use-facebook-pages";
import type { ReelDraftDTO, ApprovalStatus, PublishStatus } from "@factory/shared-types";
import { cn } from "@/lib/cn";

const LIMIT = 30;

function approvalBadgeVariant(status: ApprovalStatus) {
  if (status === "approved") return "default";
  if (status === "rejected") return "destructive";
  return "secondary";
}

function publishBadgeVariant(status: PublishStatus) {
  if (status === "published") return "default";
  if (status === "failed") return "destructive";
  if (status === "scheduled" || status === "publishing") return "secondary";
  return "outline";
}

function approvalLabel(status: ApprovalStatus) {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function publishLabel(status: PublishStatus) {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function ReelDraftCard({ draft }: { draft: ReelDraftDTO }) {
  const hashtags = draft.hashtags.slice(0, 3);

  return (
    <Link href={`/reel-drafts/${draft.id}`} className="block focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50 rounded-lg">
      <Card className="border-border/60 hover:border-border transition-colors cursor-pointer h-full">
        <CardContent className="p-0">
          {/* Thumbnail — 9:16 placeholder */}
          <div className="relative w-full bg-muted rounded-t-lg overflow-hidden" style={{ aspectRatio: "9/16", maxHeight: "240px" }}>
            <div className="absolute inset-0 flex items-center justify-center bg-gradient-to-br from-violet-500/10 to-indigo-500/10">
              <svg
                className="h-10 w-10 text-muted-foreground/40"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                aria-hidden
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z"
                />
              </svg>
            </div>
          </div>

          <div className="p-3 space-y-2">
            {/* Title */}
            <p className="text-sm font-medium leading-snug line-clamp-2">
              {draft.title ?? <span className="text-muted-foreground italic">Untitled</span>}
            </p>

            {/* Caption excerpt */}
            {draft.caption && (
              <p className="text-xs text-muted-foreground line-clamp-3 leading-relaxed">
                {draft.caption}
              </p>
            )}

            {/* Status pills */}
            <div className="flex flex-wrap gap-1.5">
              <Badge variant={approvalBadgeVariant(draft.approval_status)} className="text-[10px] px-1.5 py-0">
                {approvalLabel(draft.approval_status)}
              </Badge>
              <Badge variant={publishBadgeVariant(draft.publish_status)} className="text-[10px] px-1.5 py-0">
                {publishLabel(draft.publish_status)}
              </Badge>
            </div>

            {/* Hashtag chips */}
            {hashtags.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {hashtags.map((tag) => (
                  <span
                    key={tag}
                    className="inline-flex items-center rounded-full bg-violet-500/10 px-2 py-0.5 text-[10px] font-medium text-violet-400"
                  >
                    #{tag.replace(/^#/, "")}
                  </span>
                ))}
                {draft.hashtags.length > 3 && (
                  <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
                    +{draft.hashtags.length - 3}
                  </span>
                )}
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

function ReelDraftsPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const pageId = searchParams.get("page_id") ?? "";
  const approvalStatus = searchParams.get("approval_status") ?? "";
  const publishStatus = searchParams.get("publish_status") ?? "";
  const offset = parseInt(searchParams.get("offset") ?? "0", 10);

  const pagesQuery = useFacebookPages();
  const pages = pagesQuery.data?.pages ?? [];

  const filters = {
    ...(pageId ? { page_id: pageId } : {}),
    ...(approvalStatus ? { approval_status: approvalStatus } : {}),
    ...(publishStatus ? { publish_status: publishStatus } : {}),
    limit: LIMIT,
    offset,
  };

  const query = useReelDrafts(filters);
  const drafts = query.data?.drafts ?? [];
  const total = query.data?.total ?? 0;

  const setParam = useCallback(
    (key: string, value: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (value) params.set(key, value);
      else params.delete(key);
      params.delete("offset"); // reset pagination on filter change
      router.replace(`/reel-drafts?${params.toString()}`, { scroll: false });
    },
    [router, searchParams],
  );

  const setOffset = useCallback(
    (newOffset: number) => {
      const params = new URLSearchParams(searchParams.toString());
      if (newOffset > 0) params.set("offset", String(newOffset));
      else params.delete("offset");
      router.replace(`/reel-drafts?${params.toString()}`, { scroll: false });
    },
    [router, searchParams],
  );

  const hasPrev = offset > 0;
  const hasNext = offset + LIMIT < total;

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-1 py-1 md:px-2">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Reel Drafts</h1>
        <p className="text-sm text-muted-foreground">Review and publish your generated reels</p>
      </header>

      {/* Filter bar */}
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={pageId}
          onChange={(e) => setParam("page_id", e.target.value)}
          className="rounded-md border border-border/60 bg-background px-3 py-1.5 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50"
          aria-label="Filter by page"
        >
          <option value="">All pages</option>
          {pages.map((p) => (
            <option key={p.id} value={p.id}>
              {p.page_name}
            </option>
          ))}
        </select>

        <select
          value={approvalStatus}
          onChange={(e) => setParam("approval_status", e.target.value)}
          className="rounded-md border border-border/60 bg-background px-3 py-1.5 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50"
          aria-label="Filter by approval status"
        >
          <option value="">All approvals</option>
          <option value="pending">Pending</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
        </select>

        <select
          value={publishStatus}
          onChange={(e) => setParam("publish_status", e.target.value)}
          className="rounded-md border border-border/60 bg-background px-3 py-1.5 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50"
          aria-label="Filter by publish status"
        >
          <option value="">All statuses</option>
          <option value="draft">Draft</option>
          <option value="scheduled">Scheduled</option>
          <option value="publishing">Publishing</option>
          <option value="published">Published</option>
          <option value="failed">Failed</option>
        </select>

        {!query.isLoading && (
          <span className="ml-auto text-[11px] uppercase tracking-wider text-muted-foreground tabular-nums">
            {Math.min(offset + 1, total)}–{Math.min(offset + LIMIT, total)} of {total}
          </span>
        )}
      </div>

      {/* Grid */}
      {query.isLoading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-72 w-full" />
          ))}
        </div>
      ) : query.isError ? (
        <div className="rounded-md border border-rose-500/40 bg-rose-500/5 p-4 text-sm text-rose-300">
          Failed to load reel drafts.
        </div>
      ) : drafts.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border/60 bg-card/30 px-6 py-14 text-center">
          <span className="flex h-10 w-10 items-center justify-center rounded-full bg-violet-500/15 text-violet-300">
            <Inbox className="h-5 w-5" aria-hidden />
          </span>
          <div className="space-y-1">
            <div className="text-sm font-semibold text-foreground">No reel drafts</div>
            <p className="max-w-sm text-xs text-muted-foreground">
              {approvalStatus || publishStatus || pageId
                ? "No drafts match the current filters."
                : "Reel drafts will appear here once content sources are processed."}
            </p>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
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
            Previous
          </Button>
          <span className="text-xs text-muted-foreground tabular-nums">
            Page {Math.floor(offset / LIMIT) + 1} of {Math.ceil(total / LIMIT)}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={!hasNext}
            onClick={() => setOffset(offset + LIMIT)}
          >
            Next
          </Button>
        </div>
      )}
    </div>
  );
}

export default function ReelDraftsPage() {
  return (
    <Suspense
      fallback={
        <div className="mx-auto max-w-7xl space-y-6 px-1 py-1 md:px-2">
          <Skeleton className="h-8 w-40" />
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-72 w-full" />
            ))}
          </div>
        </div>
      }
    >
      <ReelDraftsPageInner />
    </Suspense>
  );
}
