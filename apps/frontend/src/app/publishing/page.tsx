"use client";

import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Inbox } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useReelDrafts } from "@/hooks/use-reel-drafts";
import { useFacebookPages } from "@/hooks/use-facebook-pages";
import { approveReelDraft, cancelReelSchedule } from "@/lib/api-fb";
import type { ReelDraftDTO } from "@factory/shared-types";

// ---------------------------------------------------------------------------
// Inline date helpers — no extra deps
// ---------------------------------------------------------------------------

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const future = diff < 0;
  const abs = Math.abs(diff);
  const s = Math.floor(abs / 1000);
  if (s < 60) return future ? `in ${s}s` : `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return future ? `in ${m}m` : `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return future ? `in ${h}h` : `${h}h ago`;
  const d = Math.floor(h / 24);
  return future ? `in ${d}d` : `${d}d ago`;
}

function fmtAbsolute(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

const QUEUE_STATUSES = ["scheduled", "publishing", "published", "failed"] as const;
type QueueStatus = (typeof QUEUE_STATUSES)[number];

const STATUS_LABELS: Record<QueueStatus, string> = {
  scheduled: "Scheduled",
  publishing: "Publishing",
  published: "Published",
  failed: "Failed",
};

const STATUS_BADGE_CLASS: Record<QueueStatus, string> = {
  scheduled: "bg-indigo-500/15 text-indigo-300 border-indigo-500/30",
  publishing: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  published: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  failed: "bg-rose-500/15 text-rose-300 border-rose-500/30",
};

function isQueueStatus(v: string): v is QueueStatus {
  return (QUEUE_STATUSES as readonly string[]).includes(v);
}

// ---------------------------------------------------------------------------
// Row actions
// ---------------------------------------------------------------------------

function RowActions({
  draft,
  onMutate,
}: {
  draft: ReelDraftDTO;
  onMutate: () => void;
}) {
  const status = draft.publish_status as QueueStatus;

  if (status === "published") {
    if (!draft.facebook_post_id) return null;
    return (
      <a
        href={`https://facebook.com/${draft.facebook_post_id}`}
        target="_blank"
        rel="noopener noreferrer"
        className="text-xs text-indigo-400 underline-offset-2 hover:underline"
      >
        View on Facebook
      </a>
    );
  }

  if (status === "failed") {
    return (
      <Button
        size="sm"
        variant="outline"
        className="h-7 border-rose-500/40 text-rose-300 hover:bg-rose-500/10 hover:text-rose-200"
        onClick={async () => {
          await approveReelDraft(draft.id, { publish_now: true });
          onMutate();
        }}
      >
        Retry
      </Button>
    );
  }

  if (status === "scheduled") {
    return (
      <Button
        size="sm"
        variant="outline"
        className="h-7 border-border/60 text-muted-foreground hover:text-foreground"
        onClick={async () => {
          await cancelReelSchedule(draft.id);
          onMutate();
        }}
      >
        Cancel
      </Button>
    );
  }

  return null;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function PublishingPage() {
  const qc = useQueryClient();
  const [pageFilter, setPageFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState<"" | QueueStatus>("");

  const draftsQuery = useReelDrafts({});
  const pagesQuery = useFacebookPages();

  // id → page_name lookup
  const pageMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const p of pagesQuery.data?.pages ?? []) {
      m.set(p.id, p.page_name);
    }
    return m;
  }, [pagesQuery.data]);

  const rows = useMemo(() => {
    const all = draftsQuery.data?.drafts ?? [];
    let items = all.filter((d) => isQueueStatus(d.publish_status));

    if (pageFilter) items = items.filter((d) => d.page_id === pageFilter);
    if (statusFilter) items = items.filter((d) => d.publish_status === statusFilter);

    const nonPublished = items
      .filter((d) => d.publish_status !== "published")
      .sort((a, b) => {
        const ta = a.scheduled_at ? new Date(a.scheduled_at).getTime() : 0;
        const tb = b.scheduled_at ? new Date(b.scheduled_at).getTime() : 0;
        return ta - tb;
      });

    const published = items
      .filter((d) => d.publish_status === "published")
      .sort((a, b) => {
        const ta = a.published_at ? new Date(a.published_at).getTime() : 0;
        const tb = b.published_at ? new Date(b.published_at).getTime() : 0;
        return tb - ta;
      });

    return [...nonPublished, ...published];
  }, [draftsQuery.data, pageFilter, statusFilter]);

  function invalidate() {
    qc.invalidateQueries({ queryKey: ["reel-drafts"] });
  }

  const pages = pagesQuery.data?.pages ?? [];
  const isLoading = draftsQuery.isLoading;
  const isError = draftsQuery.isError;

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-1 py-1 md:px-2">
      {/* Header */}
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Publishing Queue</h1>
        <p className="text-sm text-muted-foreground">Reels scheduled, in-flight, published, or failed</p>
      </header>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={pageFilter}
          onChange={(e) => setPageFilter(e.target.value)}
          className="h-8 rounded-md border border-border/60 bg-background px-2.5 text-xs text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50"
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
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as "" | QueueStatus)}
          className="h-8 rounded-md border border-border/60 bg-background px-2.5 text-xs text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/50"
          aria-label="Filter by status"
        >
          <option value="">All statuses</option>
          {QUEUE_STATUSES.map((s) => (
            <option key={s} value={s}>
              {STATUS_LABELS[s]}
            </option>
          ))}
        </select>

        {!isLoading && (
          <span className="ml-auto text-[11px] uppercase tracking-wider text-muted-foreground tabular-nums">
            {rows.length} {rows.length === 1 ? "reel" : "reels"}
          </span>
        )}
      </div>

      {/* Content */}
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : isError ? (
        <div className="rounded-md border border-rose-500/40 bg-rose-500/5 p-4 text-sm text-rose-300">
          Failed to load publishing queue.
        </div>
      ) : rows.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border/60 bg-card/30 px-6 py-14 text-center">
          <span className="flex h-10 w-10 items-center justify-center rounded-full bg-violet-500/15 text-violet-300">
            <Inbox className="h-5 w-5" aria-hidden />
          </span>
          <div className="space-y-1">
            <div className="text-sm font-semibold text-foreground">Nothing in the queue</div>
            <p className="max-w-sm text-xs text-muted-foreground">
              Approved reels will appear here once they are scheduled or published.
            </p>
          </div>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border/60">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border/60 bg-card/40">
                <th className="px-4 py-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  Page
                </th>
                <th className="px-4 py-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  Title
                </th>
                <th className="px-4 py-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  Scheduled
                </th>
                <th className="px-4 py-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  Status
                </th>
                <th className="px-4 py-2.5 text-right text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  Actions
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              {rows.map((draft) => {
                const status = draft.publish_status as QueueStatus;
                const pageName = pageMap.get(draft.page_id) ?? draft.page_id.slice(0, 8) + "…";
                const dateIso = draft.publish_status === "published"
                  ? draft.published_at
                  : draft.scheduled_at;

                return (
                  <tr key={draft.id} className="bg-background/60 transition-colors hover:bg-card/60">
                    <td className="whitespace-nowrap px-4 py-3 text-xs text-muted-foreground">
                      {pageName}
                    </td>
                    <td className="max-w-xs px-4 py-3">
                      <div className="line-clamp-1 text-sm font-medium text-foreground">
                        {draft.title ?? <span className="italic text-muted-foreground">Untitled</span>}
                      </div>
                      {status === "failed" && draft.error_message && (
                        <div className="mt-0.5 truncate text-xs text-rose-400" title={draft.error_message}>
                          {draft.error_message.slice(0, 120)}
                          {draft.error_message.length > 120 ? "…" : ""}
                        </div>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-xs text-muted-foreground">
                      {dateIso ? (
                        <span title={fmtAbsolute(dateIso)}>{timeAgo(dateIso)}</span>
                      ) : (
                        <span className="text-border">—</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3">
                      <Badge
                        variant="outline"
                        className={`text-[10px] font-medium tracking-wide ${STATUS_BADGE_CLASS[status]}`}
                      >
                        {STATUS_LABELS[status]}
                      </Badge>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right">
                      <RowActions draft={draft} onMutate={invalidate} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
