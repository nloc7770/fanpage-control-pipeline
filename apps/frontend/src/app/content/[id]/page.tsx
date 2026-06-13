"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ExternalLink } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PublishStatusBadge } from "@/components/publish/publish-status-badge";
import { useReelDraft } from "@/hooks/use-reel-drafts";
import {
  updateReelDraft,
  approveReelDraft,
  scheduleReelDraft,
  cancelReelSchedule,
  assetDownloadUrl,
} from "@/lib/api-fb";
import type { ReelDraftDTO } from "@factory/shared-types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function defaultScheduleTime(suggested: string | null): string {
  const base = suggested ? new Date(suggested) : new Date(Date.now() + 60 * 60 * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${base.getFullYear()}-${pad(base.getMonth() + 1)}-${pad(base.getDate())}` +
    `T${pad(base.getHours())}:${pad(base.getMinutes())}`
  );
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleString("vi-VN", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

type CombinedStatus = "draft" | "approved" | "scheduled" | "published" | "failed" | "publishing";

function getCombinedStatus(draft: ReelDraftDTO): CombinedStatus {
  if (draft.publish_status === "published") return "published";
  if (draft.publish_status === "failed") return "failed";
  if (draft.publish_status === "scheduled") return "scheduled";
  if (draft.publish_status === "publishing") return "publishing";
  if (draft.approval_status === "approved") return "approved";
  return "draft";
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ContentDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = params.id;
  const qc = useQueryClient();
  const query = useReelDraft(id);
  const draft = query.data;

  // Form state
  const [title, setTitle] = useState("");
  const [caption, setCaption] = useState("");
  const [hashtags, setHashtags] = useState<string[]>([]);
  const [hashtagInput, setHashtagInput] = useState("");

  // Schedule dialog
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [scheduleAt, setScheduleAt] = useState("");

  // Init form once
  const initialized = useRef(false);
  useEffect(() => {
    if (draft && !initialized.current) {
      setTitle(draft.title ?? "");
      setCaption(draft.caption ?? "");
      setHashtags(draft.hashtags ?? []);
      initialized.current = true;
    }
  }, [draft]);

  // Invalidation
  const invalidate = useCallback(() => {
    qc.invalidateQueries({ queryKey: ["reel-draft", id] });
    qc.invalidateQueries({ queryKey: ["reel-drafts"] });
  }, [qc, id]);

  // Mutations
  const saveMutation = useMutation({
    mutationFn: () => updateReelDraft(id, { title, caption, hashtags }),
    onSuccess: invalidate,
  });

  const approveMutation = useMutation({
    mutationFn: () => approveReelDraft(id, {}),
    onSuccess: invalidate,
  });

  const scheduleMutation = useMutation({
    mutationFn: (at: string) => scheduleReelDraft(id, at),
    onSuccess: () => {
      invalidate();
      setScheduleOpen(false);
    },
  });

  const publishNowMutation = useMutation({
    mutationFn: () => approveReelDraft(id, { publish_now: true }),
    onSuccess: invalidate,
  });

  const cancelScheduleMutation = useMutation({
    mutationFn: () => cancelReelSchedule(id),
    onSuccess: invalidate,
  });

  const anyMutating =
    saveMutation.isPending ||
    approveMutation.isPending ||
    scheduleMutation.isPending ||
    publishNowMutation.isPending ||
    cancelScheduleMutation.isPending;

  // Hashtag helpers
  function addHashtag() {
    const tag = hashtagInput.trim().replace(/^#/, "");
    if (!tag || hashtags.includes(tag)) {
      setHashtagInput("");
      return;
    }
    setHashtags((prev) => [...prev, tag]);
    setHashtagInput("");
  }

  function removeHashtag(tag: string) {
    setHashtags((prev) => prev.filter((t) => t !== tag));
  }

  // Derived
  const videoSrc = draft?.video_asset_id ? assetDownloadUrl(draft.video_asset_id) : null;
  const combinedStatus = draft ? getCombinedStatus(draft) : "draft";

  // Loading
  if (query.isLoading || !draft) {
    return (
      <div className="mx-auto max-w-5xl space-y-6 px-1 py-1 md:px-2">
        <Skeleton className="h-8 w-48" />
        <div className="grid gap-6 lg:grid-cols-2">
          <Skeleton className="w-full rounded-lg" style={{ aspectRatio: "9/16", maxHeight: "500px" }} />
          <div className="space-y-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="mx-auto max-w-5xl px-1 py-1 md:px-2">
        <div className="rounded-lg border border-rose-500/40 bg-rose-500/5 p-4 text-sm text-rose-300">
          Không thể tải nội dung.
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-1 py-1 md:px-2">
      {/* Back + header */}
      <header className="flex items-center gap-3">
        <Button asChild variant="ghost" size="icon" className="h-8 w-8">
          <Link href="/content">
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <div className="flex-1 min-w-0">
          <h1 className="text-xl font-semibold tracking-tight truncate">
            {draft.title ?? "Chưa có tiêu đề"}
          </h1>
        </div>
        <PublishStatusBadge status={combinedStatus} />
      </header>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Left: video preview */}
        <div className="flex flex-col gap-3">
          <div className="rounded-lg overflow-hidden border border-border/60 bg-black">
            <video
              controls
              className="w-full"
              style={{ aspectRatio: "9/16", maxHeight: "500px" }}
              src={videoSrc ?? undefined}
              aria-label="Xem trước video"
            >
              Trình duyệt không hỗ trợ video.
            </video>
          </div>
          {!videoSrc && (
            <p className="text-center text-xs text-muted-foreground">
              Chưa có video.
            </p>
          )}
        </div>

        {/* Right: editor + actions */}
        <div className="flex flex-col gap-5">
          {/* Published banner */}
          {combinedStatus === "published" && (
            <div className="rounded-lg border border-neon/30 bg-neon/5 px-4 py-3 text-sm text-neon">
              Đã đăng
              {draft.published_at && (
                <span className="ml-1 text-neon/70">
                  lúc {formatDate(draft.published_at)}
                </span>
              )}
              {draft.facebook_post_id && (
                <a
                  href={`https://facebook.com/${draft.facebook_post_id}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="ml-3 inline-flex items-center gap-1 underline underline-offset-2 hover:text-neon/80"
                >
                  Xem trên Facebook <ExternalLink className="h-3 w-3" />
                </a>
              )}
            </div>
          )}

          {/* Scheduled banner */}
          {combinedStatus === "scheduled" && draft.scheduled_at && (
            <div className="flex items-center justify-between rounded-lg border border-sky-500/30 bg-sky-500/5 px-4 py-3 text-sm text-sky-300">
              <span>Lên lịch: {formatDate(draft.scheduled_at)}</span>
              <Button
                variant="ghost"
                size="sm"
                className="text-sky-300 hover:text-sky-100"
                disabled={cancelScheduleMutation.isPending}
                onClick={() => cancelScheduleMutation.mutate()}
              >
                Hủy lịch
              </Button>
            </div>
          )}

          {/* Failed banner */}
          {combinedStatus === "failed" && (
            <div className="flex items-center justify-between rounded-lg border border-rose-500/30 bg-rose-500/5 px-4 py-3 text-sm text-rose-300">
              <span>{draft.error_message ?? "Đăng thất bại."}</span>
              <Button
                variant="ghost"
                size="sm"
                className="text-rose-300 hover:text-rose-100"
                disabled={publishNowMutation.isPending}
                onClick={() => publishNowMutation.mutate()}
              >
                Thử lại
              </Button>
            </div>
          )}

          {/* Title field */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Tiêu đề
            </label>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Nhập tiêu đề..."
              className="border-border/60 focus-visible:ring-neon/50"
            />
          </div>

          {/* Caption field */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Caption
              </label>
              <span className="text-[11px] tabular-nums text-muted-foreground">
                {caption.length} ký tự
              </span>
            </div>
            <textarea
              value={caption}
              onChange={(e) => setCaption(e.target.value)}
              placeholder="Viết caption..."
              className="w-full min-h-[140px] rounded-md border border-border/60 bg-background p-3 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-neon/50 resize-y"
            />
          </div>

          {/* Hashtags */}
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Hashtags
            </label>
            <div className="flex flex-wrap gap-1.5 min-h-[28px]">
              {hashtags.map((tag) => (
                <Badge key={tag} variant="secondary" className="gap-1 pr-1 bg-neon/10 text-neon border-neon/20">
                  #{tag}
                  <button
                    type="button"
                    aria-label={`Xóa #${tag}`}
                    onClick={() => removeHashtag(tag)}
                    className="ml-0.5 rounded-full hover:bg-neon/20 p-0.5 leading-none"
                  >
                    ×
                  </button>
                </Badge>
              ))}
            </div>
            <div className="flex gap-2">
              <Input
                value={hashtagInput}
                onChange={(e) => setHashtagInput(e.target.value)}
                placeholder="#hashtag"
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addHashtag();
                  }
                }}
                className="flex-1 border-border/60 focus-visible:ring-neon/50"
              />
              <Button type="button" variant="outline" size="sm" onClick={addHashtag}>
                Thêm
              </Button>
            </div>
          </div>

          {/* Save button */}
          <Button
            onClick={() => saveMutation.mutate()}
            disabled={anyMutating}
            className="w-full bg-neon text-black hover:bg-neon/90 font-medium"
          >
            {saveMutation.isPending ? "Đang lưu..." : "Lưu thay đổi"}
          </Button>

          {/* Actions — context-dependent */}
          {combinedStatus === "draft" && (
            <div className="space-y-3 border-t border-border/60 pt-4">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Hành động
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  disabled={anyMutating}
                  onClick={() => approveMutation.mutate()}
                  className="bg-neon/15 text-neon border border-neon/30 hover:bg-neon/25"
                >
                  {approveMutation.isPending ? "Đang duyệt..." : "Duyệt"}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={anyMutating}
                  onClick={() => {
                    setScheduleAt(defaultScheduleTime(draft.suggested_post_time ?? null));
                    setScheduleOpen(true);
                  }}
                >
                  Lên lịch
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={anyMutating}
                  onClick={() => publishNowMutation.mutate()}
                >
                  {publishNowMutation.isPending ? "Đang đăng..." : "Đăng ngay"}
                </Button>
              </div>
            </div>
          )}

          {combinedStatus === "approved" && (
            <div className="space-y-3 border-t border-border/60 pt-4">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Hành động
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={anyMutating}
                  onClick={() => {
                    setScheduleAt(defaultScheduleTime(draft.suggested_post_time ?? null));
                    setScheduleOpen(true);
                  }}
                >
                  Lên lịch
                </Button>
                <Button
                  size="sm"
                  disabled={anyMutating}
                  onClick={() => publishNowMutation.mutate()}
                  className="bg-neon/15 text-neon border border-neon/30 hover:bg-neon/25"
                >
                  {publishNowMutation.isPending ? "Đang đăng..." : "Đăng ngay"}
                </Button>
              </div>
            </div>
          )}

          {combinedStatus === "scheduled" && (
            <div className="space-y-3 border-t border-border/60 pt-4">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Hành động
              </p>
              <Button
                variant="outline"
                size="sm"
                disabled={anyMutating}
                onClick={() => cancelScheduleMutation.mutate()}
                className="border-rose-500/30 text-rose-400 hover:bg-rose-500/10"
              >
                {cancelScheduleMutation.isPending ? "Đang hủy..." : "Hủy lịch"}
              </Button>
            </div>
          )}
        </div>
      </div>

      {/* Schedule dialog */}
      <Dialog open={scheduleOpen} onOpenChange={setScheduleOpen}>
        <DialogContent className="border-border/60 bg-card">
          <DialogHeader>
            <DialogTitle>Lên lịch đăng</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <label className="text-sm text-muted-foreground">Thời gian đăng</label>
            <input
              type="datetime-local"
              value={scheduleAt}
              onChange={(e) => setScheduleAt(e.target.value)}
              className="w-full rounded-md border border-border/60 bg-background px-3 py-2 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-neon/50"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setScheduleOpen(false)}>
              Hủy
            </Button>
            <Button
              disabled={!scheduleAt || scheduleMutation.isPending}
              onClick={() => {
                if (!scheduleAt) return;
                scheduleMutation.mutate(new Date(scheduleAt).toISOString());
              }}
              className="bg-neon text-black hover:bg-neon/90"
            >
              {scheduleMutation.isPending ? "Đang lên lịch..." : "Xác nhận"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
