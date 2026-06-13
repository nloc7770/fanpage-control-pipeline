"use client";

import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Calendar, Check, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { approveReelDraft, scheduleReelDraft, updateReelDraft } from "@/lib/api-fb";
import { PublishStatusBadge } from "@/components/publish/publish-status-badge";
import type { ReelDraftDTO, ApprovalStatus, PublishStatus } from "@factory/shared-types";
import { cn } from "@/lib/cn";

// ---------------------------------------------------------------------------
// Status mapping
// ---------------------------------------------------------------------------

type CombinedStatus = "draft" | "approved" | "scheduled" | "published" | "failed" | "publishing";

function getCombinedStatus(draft: ReelDraftDTO): CombinedStatus {
  if (draft.publish_status === "published") return "published";
  if (draft.publish_status === "failed") return "failed";
  if (draft.publish_status === "scheduled") return "scheduled";
  if (draft.publish_status === "publishing") return "publishing";
  if (draft.approval_status === "approved") return "approved";
  return "draft";
}

const STATUS_LABELS: Record<CombinedStatus, string> = {
  draft: "Nháp",
  approved: "Đã duyệt",
  scheduled: "Đã lên lịch",
  published: "Đã đăng",
  failed: "Lỗi",
  publishing: "Đang đăng",
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatScheduledTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  return new Date(iso).toLocaleString("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface ReelDraftCardProps {
  draft: ReelDraftDTO;
}

export function ReelDraftCard({ draft }: ReelDraftCardProps) {
  const qc = useQueryClient();
  const combinedStatus = getCombinedStatus(draft);
  const scheduledLabel = formatScheduledTime(draft.scheduled_at);

  function invalidate() {
    qc.invalidateQueries({ queryKey: ["reel-drafts"] });
  }

  const approveMutation = useMutation({
    mutationFn: () => approveReelDraft(draft.id, {}),
    onSuccess: invalidate,
  });

  const deleteMutation = useMutation({
    mutationFn: () => updateReelDraft(draft.id, { title: draft.title ?? "" }),
    onSuccess: invalidate,
  });

  return (
    <div className="group relative">
      <Link
        href={`/content/${draft.id}`}
        className="block focus:outline-none focus-visible:ring-2 focus-visible:ring-neon/50 rounded-lg"
      >
        <Card className="border-border/60 hover:border-neon/30 transition-all duration-200 cursor-pointer h-full bg-card/60 backdrop-blur-sm">
          <CardContent className="p-0">
            {/* Thumbnail placeholder 9:16 */}
            <div
              className="relative w-full bg-muted/50 rounded-t-lg overflow-hidden"
              style={{ aspectRatio: "9/16", maxHeight: "200px" }}
            >
              <div className="absolute inset-0 flex items-center justify-center bg-gradient-to-br from-neon/5 to-emerald-900/10">
                <svg
                  className="h-8 w-8 text-muted-foreground/30"
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

              {/* Status badge overlaid */}
              <div className="absolute top-2 left-2">
                <PublishStatusBadge status={combinedStatus} />
              </div>
            </div>

            <div className="p-3 space-y-2">
              {/* Title */}
              <p className="text-sm font-medium leading-snug line-clamp-2 text-foreground">
                {draft.title ?? (
                  <span className="text-muted-foreground italic">Chưa có tiêu đề</span>
                )}
              </p>

              {/* Caption preview */}
              {draft.caption && (
                <p className="text-xs text-muted-foreground line-clamp-2 leading-relaxed">
                  {draft.caption}
                </p>
              )}

              {/* Scheduled time */}
              {scheduledLabel && (
                <div className="flex items-center gap-1.5 text-[11px] text-sky-400">
                  <Calendar className="h-3 w-3" />
                  <span>{scheduledLabel}</span>
                </div>
              )}

              {/* Hashtag chips */}
              {draft.hashtags.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {draft.hashtags.slice(0, 3).map((tag) => (
                    <span
                      key={tag}
                      className="inline-flex items-center rounded-full bg-neon/10 px-2 py-0.5 text-[10px] font-medium text-neon/80"
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

      {/* Action buttons — show on hover (desktop) or always visible (mobile) */}
      {combinedStatus === "draft" && (
        <div className="absolute bottom-3 right-3 flex gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity md:opacity-0 max-md:opacity-100">
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 bg-card/90 backdrop-blur-sm border border-border/60 hover:bg-neon/10 hover:text-neon hover:border-neon/30"
            title="Duyệt"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              approveMutation.mutate();
            }}
            disabled={approveMutation.isPending}
          >
            <Check className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}
    </div>
  );
}

export default ReelDraftCard;
