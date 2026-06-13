"use client";

import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Inbox, Calendar as CalendarIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { PublishStatusBadge } from "@/components/publish/publish-status-badge";
import { useReelDrafts } from "@/hooks/use-reel-drafts";
import { approveReelDraft, cancelReelSchedule } from "@/lib/api-fb";
import type { ReelDraftDTO } from "@factory/shared-types";
import { cn } from "@/lib/cn";

// ---------------------------------------------------------------------------
// Types & constants
// ---------------------------------------------------------------------------

type QueueTab = "scheduled" | "publishing" | "published" | "failed";

const TABS: { value: QueueTab; label: string }[] = [
  { value: "scheduled", label: "Đã lên lịch" },
  { value: "publishing", label: "Đang đăng" },
  { value: "published", label: "Đã đăng" },
  { value: "failed", label: "Lỗi" },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const future = diff < 0;
  const abs = Math.abs(diff);
  const s = Math.floor(abs / 1000);
  if (s < 60) return future ? `trong ${s}s` : `${s}s trước`;
  const m = Math.floor(s / 60);
  if (m < 60) return future ? `trong ${m} phút` : `${m} phút trước`;
  const h = Math.floor(m / 60);
  if (h < 24) return future ? `trong ${h} giờ` : `${h} giờ trước`;
  const d = Math.floor(h / 24);
  return future ? `trong ${d} ngày` : `${d} ngày trước`;
}

function fmtAbsolute(iso: string): string {
  return new Date(iso).toLocaleString("vi-VN", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function getDaysWithPosts(drafts: ReelDraftDTO[]): Set<string> {
  const days = new Set<string>();
  for (const d of drafts) {
    const iso = d.scheduled_at ?? d.published_at;
    if (iso) {
      days.add(new Date(iso).toISOString().slice(0, 10));
    }
  }
  return days;
}

// ---------------------------------------------------------------------------
// Mini calendar widget
// ---------------------------------------------------------------------------

function MiniCalendar({ activeDays }: { activeDays: Set<string> }) {
  const today = new Date();
  const year = today.getFullYear();
  const month = today.getMonth();
  const firstDay = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();

  const monthName = today.toLocaleString("vi-VN", { month: "long", year: "numeric" });

  const cells: (number | null)[] = [];
  for (let i = 0; i < firstDay; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);

  return (
    <div className="rounded-lg border border-border/60 bg-card/40 p-4">
      <div className="flex items-center gap-2 mb-3">
        <CalendarIcon className="h-4 w-4 text-neon" />
        <span className="text-sm font-medium capitalize">{monthName}</span>
      </div>
      <div className="grid grid-cols-7 gap-1 text-center">
        {["CN", "T2", "T3", "T4", "T5", "T6", "T7"].map((d) => (
          <div key={d} className="text-[10px] text-muted-foreground font-medium py-1">
            {d}
          </div>
        ))}
        {cells.map((day, i) => {
          if (day === null) return <div key={`empty-${i}`} />;
          const dateStr = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
          const isToday = day === today.getDate();
          const hasPost = activeDays.has(dateStr);

          return (
            <div
              key={day}
              className={cn(
                "relative flex items-center justify-center h-7 w-7 mx-auto rounded-md text-xs transition-colors",
                isToday && "bg-neon/10 text-neon font-bold",
                !isToday && "text-muted-foreground",
              )}
            >
              {day}
              {hasPost && (
                <span
                  className={cn(
                    "absolute bottom-0.5 left-1/2 -translate-x-1/2 h-1 w-1 rounded-full",
                    isToday ? "bg-neon shadow-[0_0_4px_rgba(57,255,20,0.6)]" : "bg-sky-400",
                  )}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
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
  const status = draft.publish_status;

  if (status === "published") {
    if (!draft.facebook_post_id) return null;
    return (
      <a
        href={`https://facebook.com/${draft.facebook_post_id}`}
        target="_blank"
        rel="noopener noreferrer"
        className="text-xs text-neon underline-offset-2 hover:underline"
      >
        Xem
      </a>
    );
  }

  if (status === "failed") {
    return (
      <Button
        size="sm"
        variant="outline"
        className="h-7 text-xs border-rose-500/30 text-rose-300 hover:bg-rose-500/10 hover:text-rose-200"
        onClick={async () => {
          await approveReelDraft(draft.id, { publish_now: true });
          onMutate();
        }}
      >
        Thử lại
      </Button>
    );
  }

  if (status === "scheduled") {
    return (
      <Button
        size="sm"
        variant="outline"
        className="h-7 text-xs border-border/60 text-muted-foreground hover:text-foreground"
        onClick={async () => {
          await cancelReelSchedule(draft.id);
          onMutate();
        }}
      >
        Hủy
      </Button>
    );
  }

  return null;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function PublishPage() {
  const qc = useQueryClient();
  const [tabFilter, setTabFilter] = useState<QueueTab>("scheduled");

  const draftsQuery = useReelDrafts({});

  // Filter to queue items only
  const queueDrafts = useMemo(() => {
    const all = draftsQuery.data?.drafts ?? [];
    return all.filter((d) =>
      ["scheduled", "publishing", "published", "failed"].includes(d.publish_status),
    );
  }, [draftsQuery.data?.drafts]);

  // Filtered rows by current tab
  const rows = useMemo(() => {
    const items = queueDrafts.filter((d) => d.publish_status === tabFilter);

    return items.sort((a, b) => {
      if (a.publish_status === "published" && b.publish_status === "published") {
        const ta = a.published_at ? new Date(a.published_at).getTime() : 0;
        const tb = b.published_at ? new Date(b.published_at).getTime() : 0;
        return tb - ta;
      }
      const ta = a.scheduled_at ? new Date(a.scheduled_at).getTime() : Infinity;
      const tb = b.scheduled_at ? new Date(b.scheduled_at).getTime() : Infinity;
      return ta - tb;
    });
  }, [queueDrafts, tabFilter]);

  // Calendar dots
  const activeDays = useMemo(() => getDaysWithPosts(queueDrafts), [queueDrafts]);

  function invalidate() {
    qc.invalidateQueries({ queryKey: ["reel-drafts"] });
  }

  const isLoading = draftsQuery.isLoading;
  const isError = draftsQuery.isError;

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-1 py-1 md:px-2">
      {/* Header */}
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Lịch đăng</h1>
        <p className="text-sm text-muted-foreground">
          Quản lý lịch đăng bài và theo dõi trạng thái
        </p>
      </header>

      {/* Mini calendar */}
      <MiniCalendar activeDays={activeDays} />

      {/* Tabs */}
      <Tabs value={tabFilter} onValueChange={(v) => setTabFilter(v as QueueTab)}>
        <TabsList className="bg-card/60 border border-border/60 h-auto flex-wrap">
          {TABS.map((tab) => (
            <TabsTrigger
              key={tab.value}
              value={tab.value}
              className="text-xs data-[state=active]:bg-neon/15 data-[state=active]:text-neon data-[state=active]:shadow-none"
            >
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      {/* Content */}
      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full rounded-lg" />
          ))}
        </div>
      ) : isError ? (
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/5 p-4 text-sm text-rose-300">
          Không thể tải lịch đăng.
        </div>
      ) : rows.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed border-border/60 bg-card/30 px-6 py-14 text-center">
          <span className="flex h-10 w-10 items-center justify-center rounded-full bg-neon/10 text-neon">
            <Inbox className="h-5 w-5" aria-hidden />
          </span>
          <div className="space-y-1">
            <div className="text-sm font-semibold text-foreground">Chưa có bài nào</div>
            <p className="max-w-sm text-xs text-muted-foreground">
              Bài đã lên lịch hoặc đã đăng sẽ xuất hiện ở đây.
            </p>
          </div>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border/60">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border/60 bg-card/40">
                <th className="px-4 py-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  Tiêu đề
                </th>
                <th className="px-4 py-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-muted-foreground hidden sm:table-cell">
                  Thời gian
                </th>
                <th className="px-4 py-2.5 text-left text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  Trạng thái
                </th>
                <th className="px-4 py-2.5 text-right text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  Hành động
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/40">
              {rows.map((draft) => {
                const dateIso =
                  draft.publish_status === "published"
                    ? draft.published_at
                    : draft.scheduled_at;

                return (
                  <tr
                    key={draft.id}
                    className="bg-background/60 transition-colors hover:bg-card/60"
                  >
                    <td className="max-w-xs px-4 py-3">
                      <div className="line-clamp-1 text-sm font-medium text-foreground">
                        {draft.title ?? (
                          <span className="italic text-muted-foreground">Chưa có tiêu đề</span>
                        )}
                      </div>
                      {draft.publish_status === "failed" && draft.error_message && (
                        <div
                          className="mt-0.5 truncate text-xs text-rose-400"
                          title={draft.error_message}
                        >
                          {draft.error_message.slice(0, 100)}
                        </div>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-xs text-muted-foreground hidden sm:table-cell">
                      {dateIso ? (
                        <span title={fmtAbsolute(dateIso)}>{timeAgo(dateIso)}</span>
                      ) : (
                        <span className="text-border">—</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3">
                      <PublishStatusBadge status={draft.publish_status} />
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
