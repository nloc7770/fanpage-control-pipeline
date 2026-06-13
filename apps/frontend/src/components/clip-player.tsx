"use client";

import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Download, Film, Link2, Share2, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/cn";

export interface ClipPlayerProps {
  src: string | null;
  poster?: string | null;
  title?: string;
}

export function ClipPlayer({ src, poster, title }: ClipPlayerProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [copied, setCopied] = useState(false);

  // Spacebar toggles play/pause when the wrapper has focus.
  const handleKeyDown = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    if (event.code !== "Space" && event.key !== " ") return;
    const v = videoRef.current;
    if (!v || !src) return;
    event.preventDefault();
    if (v.paused) {
      void v.play();
    } else {
      v.pause();
    }
  }, [src]);

  // Reset the copied flag after a moment so the button can be reused.
  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(t);
  }, [copied]);

  const handleCopyLink = useCallback(async () => {
    if (!src) return;
    // Resolve to absolute URL so the copy is useful from another tab.
    const absolute = (() => {
      if (typeof window === "undefined") return src;
      try {
        return new URL(src, window.location.origin).toString();
      } catch {
        return src;
      }
    })();
    try {
      await navigator.clipboard.writeText(absolute);
      setCopied(true);
    } catch {
      // Silent: clipboard may be unavailable (insecure context).
    }
  }, [src]);

  return (
    <TooltipProvider delayDuration={200}>
      <div className="space-y-3">
        <div
          tabIndex={0}
          role="group"
          aria-label={title ? `Clip player: ${title}` : "Clip player"}
          onKeyDown={handleKeyDown}
          className={cn(
            "relative mx-auto aspect-[9/16] w-full max-w-sm overflow-hidden rounded-xl bg-black ring-1 ring-white/5",
            "focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500",
          )}
        >
          {src ? (
            <video
              ref={videoRef}
              key={src}
              src={src}
              poster={poster ?? undefined}
              controls
              playsInline
              preload="metadata"
              className="h-full w-full"
              aria-label={title ?? "Clip video"}
            />
          ) : (
            <div className="flex h-full w-full flex-col items-center justify-center gap-2 px-6 text-center">
              <Film className="h-10 w-10 text-white/30" />
              <p className="text-sm font-medium text-white/80">Clip not yet rendered</p>
              <p className="text-xs text-white/50">
                The render worker will populate this player once the MP4 is ready.
              </p>
            </div>
          )}
        </div>

        <div className="flex flex-wrap items-center justify-center gap-1">
          {src ? (
            <Button asChild variant="ghost" size="sm">
              <a href={src} download aria-label="Download clip">
                <Download className="h-4 w-4" />
                Download
              </a>
            </Button>
          ) : null}

          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                onClick={handleCopyLink}
                disabled={!src}
                aria-label="Copy link to clip"
              >
                {copied ? (
                  <>
                    <Check className="h-4 w-4 text-emerald-400" />
                    Copied
                  </>
                ) : (
                  <>
                    <Link2 className="h-4 w-4" />
                    Copy link
                  </>
                )}
              </Button>
            </TooltipTrigger>
            <TooltipContent>
              {src ? "Copy direct link to MP4" : "Link available once rendered"}
            </TooltipContent>
          </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              {/* Wrap disabled button so the tooltip still fires. */}
              <span tabIndex={0} className="inline-flex">
                <Button
                  variant="ghost"
                  size="sm"
                  disabled
                  aria-label="Share clip (coming soon)"
                >
                  <Share2 className="h-4 w-4" />
                  Share
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent>Sharing is coming soon</TooltipContent>
          </Tooltip>
        </div>
      </div>
    </TooltipProvider>
  );
}

export default ClipPlayer;
