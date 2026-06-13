"use client";

import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Clock, Film, ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ScriptStatusBadge } from "@/components/scripts/script-status-badge";
import { ScriptViewer } from "@/components/scripts/script-viewer";
import { CreateFromScriptDialog } from "@/components/scripts/create-from-script-dialog";
import { useScript, useUpdateScriptStatus } from "@/hooks/use-scripts";
import type { ScriptStatus } from "@/hooks/use-scripts";
import { cn } from "@/lib/cn";

const STATUS_OPTIONS: { value: ScriptStatus; label: string }[] = [
  { value: "unfilmed", label: "Chưa quay" },
  { value: "filmed", label: "Đã quay" },
  { value: "published", label: "Đã đăng" },
];

export default function ScriptDetailPage() {
  const params = useParams();
  const router = useRouter();
  const slug = params.slug as string;

  const { data: script, isLoading, error } = useScript(slug);
  const updateStatus = useUpdateScriptStatus();

  const [statusDropdownOpen, setStatusDropdownOpen] = useState(false);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);

  function handleStatusChange(newStatus: ScriptStatus) {
    if (!script || newStatus === script.status) return;
    updateStatus.mutate({ slug, status: newStatus });
    setStatusDropdownOpen(false);
  }

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 w-full rounded-lg" />
      </div>
    );
  }

  if (error || !script) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => router.push("/scripts")}>
          <ArrowLeft className="h-4 w-4 mr-1" /> Quay lại
        </Button>
        <div className="text-center py-12">
          <p className="text-sm text-destructive">Không tìm thấy kịch bản.</p>
        </div>
      </div>
    );
  }

  const durationLabel = script.duration_seconds >= 60
    ? `${Math.floor(script.duration_seconds / 60)} phút ${script.duration_seconds % 60 > 0 ? `${script.duration_seconds % 60} giây` : ""}`.trim()
    : `${script.duration_seconds} giây`;

  return (
    <div className="space-y-6">
      {/* Back + Title */}
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" onClick={() => router.push("/scripts")} aria-label="Quay lại">
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <h1 className="text-lg font-bold text-foreground flex-1 line-clamp-1">{script.title}</h1>
      </div>

      {/* Top Bar: status + duration + actions */}
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border/60 bg-card p-4">
        {/* Status dropdown */}
        <div className="relative">
          <button
            type="button"
            onClick={() => setStatusDropdownOpen(!statusDropdownOpen)}
            className="flex items-center gap-1.5 rounded-md border border-border/60 px-3 py-1.5 text-sm transition-colors hover:bg-muted"
          >
            <ScriptStatusBadge status={script.status} />
            <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
          </button>
          {statusDropdownOpen && (
            <>
              <div
                className="fixed inset-0 z-40"
                onClick={() => setStatusDropdownOpen(false)}
              />
              <div className="absolute left-0 top-full mt-1 z-50 min-w-[140px] rounded-md border border-border bg-popover p-1 shadow-md">
                {STATUS_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => handleStatusChange(opt.value)}
                    className={cn(
                      "w-full rounded-sm px-3 py-1.5 text-left text-sm transition-colors hover:bg-muted",
                      opt.value === script.status && "font-semibold text-neon",
                    )}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>

        {/* Duration */}
        <div className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <Clock className="h-4 w-4" />
          <span>{durationLabel}</span>
        </div>

        {/* Spacer */}
        <div className="flex-1" />

        {/* CTA */}
        <Button
          onClick={() => setCreateDialogOpen(true)}
          className="bg-neon text-black hover:bg-neon/90 font-semibold"
        >
          <Film className="h-4 w-4 mr-1.5" />
          Tạo Reel
        </Button>
      </div>

      {/* Script Content */}
      <div className="rounded-lg border border-border/60 bg-card p-5 md:p-8">
        <ScriptViewer content={script.content} />
      </div>

      {/* Create Dialog */}
      {createDialogOpen && (
        <CreateFromScriptDialog
          script={script}
          open={createDialogOpen}
          onOpenChange={setCreateDialogOpen}
        />
      )}
    </div>
  );
}
