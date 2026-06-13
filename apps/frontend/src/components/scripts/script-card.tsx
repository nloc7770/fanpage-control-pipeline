"use client";

import Link from "next/link";
import { Clock } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { ScriptStatusBadge } from "./script-status-badge";
import { cn } from "@/lib/cn";
import type { ScriptDTO } from "@/hooks/use-scripts";

interface ScriptCardProps {
  script: ScriptDTO;
}

export function ScriptCard({ script }: ScriptCardProps) {
  const durationLabel = script.duration_seconds >= 60
    ? `${Math.floor(script.duration_seconds / 60)} phút ${script.duration_seconds % 60 > 0 ? `${script.duration_seconds % 60} giây` : ""}`.trim()
    : `${script.duration_seconds} giây`;

  return (
    <Link
      href={`/scripts/${script.slug}`}
      className="block focus:outline-none focus-visible:ring-2 focus-visible:ring-neon/50 rounded-lg"
    >
      <Card className={cn(
        "border-border/60 transition-all cursor-pointer h-full",
        "hover:border-neon/40 hover:shadow-[0_0_20px_rgba(57,255,20,0.08)]",
      )}>
        <CardContent className="p-5 flex flex-col gap-3 h-full">
          <div className="flex items-start justify-between gap-2">
            <h3 className="text-sm font-semibold text-foreground leading-tight line-clamp-2 flex-1">
              {script.title}
            </h3>
            <ScriptStatusBadge status={script.status} />
          </div>

          <p className="text-xs text-muted-foreground line-clamp-2 flex-1">
            {script.hook || "Chưa có hook"}
          </p>

          <div className="flex items-center gap-1.5 text-xs text-muted-foreground mt-auto">
            <Clock className="h-3.5 w-3.5" />
            <span>{durationLabel}</span>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

export default ScriptCard;
