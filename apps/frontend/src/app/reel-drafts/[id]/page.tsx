"use client";

import { useState, useEffect, useRef } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
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
import { useReelDraft } from "@/hooks/use-reel-drafts";
import {
  updateReelDraft,
  approveReelDraft,
  rejectReelDraft,
  regenerateReelCaption,
  cancelReelSchedule,
  assetDownloadUrl,
} from "@/lib/api-fb";
import type { ReelDraftDTO, ClipDTO } from "@factory/shared-types";

// The API may return a nested clip object even though the shared DTO doesn't
// declare it — handle it gracefully.
type ReelDraftWithClip = ReelDraftDTO & { clip?: ClipDTO };

function defaultScheduleTime(suggested: string | null): string {
  const base = suggested ? new Date(suggested) : new Date(Date.now() + 60 * 60 * 1000);
  // datetime-local expects "YYYY-MM-DDTHH:mm" in local time
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${base.getFullYear()}-${pad(base.getMonth() + 1)}-${pad(base.getDate())}` +
    `T${pad(base.getHours())}:${pad(base.getMinutes())}`
  );
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export default function ReelDraftDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const qc = useQueryClient();
  const query = useReelDraft(id);
  const draft = query.data as ReelDraftWithClip | undefined;

  // ---- controlled form state ------------------------------------------------
  const [title, setTitle] = useState("");
  const [caption, setCaption] = useState("");
  const [hashtags, setHashtags] = useState<string[]>([]);
  const [hashtagInput, setHashtagInput] = useState("");

  // ---- dialog state ---------------------------------------------------------
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [scheduleAt, setScheduleAt] = useState("");
  const [rejectOpen, setRejectOpen] = useState(false);
  const [rejectReason, setRejectReason] = useState("");

  // Initialise form once when draft first loads; don't overwrite user edits on refetch
  const initialized = useRef(false);
  useEffect(() => {
    if (draft && !initialized.current) {
      setTitle(draft.title ?? "");
      setCaption(draft.caption ?? "");
      setHashtags(draft.hashtags ?? []);
      initialized.current = true;
    }
  }, [draft]);

  // ---- invalidation helper --------------------------------------------------
  function invalidate() {
    qc.invalidateQueries({ queryKey: ["reel-draft", id] });
    qc.invalidateQueries({ queryKey: ["reel-drafts"] });
  }

  // ---- mutations ------------------------------------------------------------
  const saveMutation = useMutation({
    mutationFn: () => updateReelDraft(id, { title, caption, hashtags }),
    onSuccess: invalidate,
  });

  const approveMutation = useMutation({
    mutationFn: (body: Parameters<typeof approveReelDraft>[1]) =>
      approveReelDraft(id, body),
    onSuccess: () => {
      invalidate();
      setScheduleOpen(false);
    },
  });

  const rejectMutation = useMutation({
    mutationFn: () => rejectReelDraft(id, rejectReason || undefined),
    onSuccess: () => {
      invalidate();
      setRejectOpen(false);
      setRejectReason("");
    },
  });

  const regenMutation = useMutation({
    mutationFn: () => regenerateReelCaption(id),
    onSuccess: (updated) => {
      // Sync caption from regenerated draft
      if (updated?.caption != null) setCaption(updated.caption);
      if (updated?.hashtags) setHashtags(updated.hashtags);
      invalidate();
    },
  });

  const cancelScheduleMutation = useMutation({
    mutationFn: () => cancelReelSchedule(id),
    onSuccess: invalidate,
  });

  // ---- hashtag helpers ------------------------------------------------------
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

  // ---- derived values -------------------------------------------------------
  const videoAssetId = draft?.video_asset_id ?? null;
  const videoSrc = assetDownloadUrl(videoAssetId);
  const isPending = draft?.approval_status === "pending";
  const isApprovedScheduled =
    draft?.approval_status === "approved" && draft?.publish_status === "scheduled";
  const isPublished = draft?.publish_status === "published";
  const isFailed = draft?.publish_status === "failed";
  const anyMutating =
    saveMutation.isPending ||
    approveMutation.isPending ||
    rejectMutation.isPending ||
    regenMutation.isPending ||
    cancelScheduleMutation.isPending;

  // ---- render ---------------------------------------------------------------
  if (query.isLoading || !draft) {
    return (
      <div className="mx-auto max-w-7xl space-y-6 px-1 py-1 md:px-2">
        <Skeleton className="h-8 w-48" />
        <div className="grid gap-6 lg:grid-cols-2">
          <Skeleton className="w-full" style={{ aspectRatio: "9/16", maxHeight: "600px" }} />
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
      <div className="mx-auto max-w-7xl px-1 py-1 md:px-2">
        <div className="rounded-md border border-rose-500/40 bg-rose-500/5 p-4 text-sm text-rose-300">
          Failed to load reel draft.
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6 px-1 py-1 md:px-2">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">
          {draft.title ?? "Untitled Reel"}
        </h1>
        <p className="text-xs text-muted-foreground">ID: {draft.id}</p>
      </header>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* ---- Left: video preview ---------------------------------------- */}
        <div className="flex flex-col gap-3">
          <video
            controls
            className="w-full rounded-lg bg-black"
            style={{ aspectRatio: "9/16", maxHeight: "600px" }}
            src={videoSrc ?? undefined}
            aria-label="Reel preview"
          >
            Your browser does not support the video element.
          </video>
          {!videoSrc && (
            <p className="text-center text-xs text-muted-foreground">
              No video asset available yet.
            </p>
          )}
        </div>

        {/* ---- Right: editor + actions ------------------------------------ */}
        <div className="flex flex-col gap-5">
          {/* Published banner */}
          {isPublished && (
            <div className="rounded-md border border-emerald-500/40 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300">
              Published
              {draft.published_at && (
                <span className="ml-1 text-emerald-400/70">
                  on {formatDate(draft.published_at)}
                </span>
              )}
              {draft.facebook_post_id && (
                <a
                  href={`https://facebook.com/${draft.facebook_post_id}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="ml-3 underline underline-offset-2 hover:text-emerald-200"
                >
                  View on Facebook
                </a>
              )}
            </div>
          )}

          {/* Scheduled banner */}
          {isApprovedScheduled && draft.scheduled_at && (
            <div className="flex items-center justify-between rounded-md border border-violet-500/40 bg-violet-500/10 px-4 py-3 text-sm text-violet-300">
              <span>Scheduled for {formatDate(draft.scheduled_at)}</span>
              <Button
                variant="ghost"
                size="sm"
                className="text-violet-300 hover:text-violet-100"
                disabled={cancelScheduleMutation.isPending}
                onClick={() => cancelScheduleMutation.mutate()}
              >
                Cancel schedule
              </Button>
            </div>
          )}

          {/* Failed banner */}
          {isFailed && (
            <div className="flex items-center justify-between rounded-md border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-300">
              <span>{draft.error_message ?? "Publish failed."}</span>
              <Button
                variant="ghost"
                size="sm"
                className="text-rose-300 hover:text-rose-100"
                disabled={approveMutation.isPending}
                onClick={() => approveMutation.mutate({ publish_now: true })}
              >
                Retry
              </Button>
            </div>
          )}

          {/* Title */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Title
            </label>
            <Input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Reel title"
            />
          </div>

          {/* Caption */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Caption
              </label>
              <span className="text-[11px] tabular-nums text-muted-foreground">
                {caption.length} chars
              </span>
            </div>
            <textarea
              value={caption}
              onChange={(e) => setCaption(e.target.value)}
              placeholder="Write a caption…"
              className="w-full min-h-[140px] rounded-md border border-input bg-background p-2 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-ring resize-y"
            />
          </div>

          {/* Hashtags */}
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Hashtags
            </label>
            <div className="flex flex-wrap gap-1.5 min-h-[28px]">
              {hashtags.map((tag) => (
                <Badge key={tag} variant="secondary" className="gap-1 pr-1">
                  #{tag}
                  <button
                    type="button"
                    aria-label={`Remove #${tag}`}
                    onClick={() => removeHashtag(tag)}
                    className="ml-0.5 rounded-full hover:bg-muted-foreground/20 p-0.5 leading-none"
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
                className="flex-1"
              />
              <Button type="button" variant="outline" size="sm" onClick={addHashtag}>
                Add
              </Button>
            </div>
          </div>

          {/* Suggested post time */}
          {draft.suggested_post_time && (
            <div className="flex items-center justify-between rounded-md border border-border/60 bg-card/50 px-3 py-2 text-sm">
              <span className="text-muted-foreground">
                Suggested: {formatDate(draft.suggested_post_time)}
              </span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  setScheduleAt(defaultScheduleTime(draft.suggested_post_time));
                  setScheduleOpen(true);
                }}
              >
                Use this
              </Button>
            </div>
          )}

          {/* Save */}
          <Button
            onClick={() => saveMutation.mutate()}
            disabled={anyMutating}
            className="w-full"
          >
            {saveMutation.isPending ? "Saving…" : "Save"}
          </Button>

          {/* Action row — only when pending */}
          {isPending && (
            <div className="space-y-2 border-t border-border/60 pt-4">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                Actions
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="default"
                  size="sm"
                  disabled={anyMutating}
                  onClick={() => approveMutation.mutate({ publish_now: true })}
                >
                  {approveMutation.isPending ? "Publishing…" : "Approve & Publish Now"}
                </Button>

                <Button
                  variant="outline"
                  size="sm"
                  disabled={anyMutating}
                  onClick={() => {
                    setScheduleAt(defaultScheduleTime(draft.suggested_post_time));
                    setScheduleOpen(true);
                  }}
                >
                  Approve & Schedule
                </Button>

                <Button
                  variant="outline"
                  size="sm"
                  disabled={anyMutating}
                  onClick={() => setRejectOpen(true)}
                  className="text-rose-400 border-rose-500/40 hover:bg-rose-500/10 hover:text-rose-300"
                >
                  Reject
                </Button>

                <Button
                  variant="ghost"
                  size="sm"
                  disabled={anyMutating}
                  onClick={() => regenMutation.mutate()}
                >
                  {regenMutation.isPending ? "Regenerating…" : "Regenerate Caption"}
                </Button>
              </div>
              {(approveMutation.isError || rejectMutation.isError || regenMutation.isError) && (
                <p className="text-xs text-rose-400">
                  {(
                    (approveMutation.error ?? rejectMutation.error ?? regenMutation.error) as Error
                  )?.message ?? "Action failed."}
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ---- Schedule dialog ----------------------------------------------- */}
      <Dialog open={scheduleOpen} onOpenChange={setScheduleOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Schedule Reel</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <label className="text-sm text-muted-foreground">Publish at</label>
            <input
              type="datetime-local"
              value={scheduleAt}
              onChange={(e) => setScheduleAt(e.target.value)}
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setScheduleOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={!scheduleAt || approveMutation.isPending}
              onClick={() => {
                if (!scheduleAt) return;
                approveMutation.mutate({
                  scheduled_at: new Date(scheduleAt).toISOString(),
                });
              }}
            >
              {approveMutation.isPending ? "Scheduling…" : "Confirm"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ---- Reject dialog ------------------------------------------------- */}
      <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject Reel</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-2">
            <label className="text-sm text-muted-foreground">
              Reason <span className="text-muted-foreground/60">(optional)</span>
            </label>
            <textarea
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="Explain why this reel is being rejected…"
              className="w-full min-h-[100px] rounded-md border border-input bg-background p-2 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-ring resize-y"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRejectOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={rejectMutation.isPending}
              onClick={() => rejectMutation.mutate()}
            >
              {rejectMutation.isPending ? "Rejecting…" : "Reject"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
