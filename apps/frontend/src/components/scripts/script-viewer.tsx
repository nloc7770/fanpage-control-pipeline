"use client";

import { cn } from "@/lib/cn";

interface ScriptViewerProps {
  content: string;
  className?: string;
}

function parseSection(line: string): { isHeading: boolean; text: string } {
  // Detect section headings like "## Hook", "## Cảnh 1", "**Hook:**", etc.
  const hashMatch = line.match(/^#{1,3}\s+(.+)/);
  if (hashMatch) return { isHeading: true, text: hashMatch[1] as string };

  const boldMatch = line.match(/^\*\*(.+?)\*\*:?$/);
  if (boldMatch) return { isHeading: true, text: boldMatch[1] as string };

  return { isHeading: false, text: line };
}

function isTimestamp(text: string): boolean {
  return /^\[?\d{1,2}:\d{2}/.test(text.trim()) || /^\(\d+s?\s*[-–]\s*\d+s?\)/.test(text.trim());
}

function isVoiceover(text: string): boolean {
  return /^(VO|Voiceover|Lời thoại|Narration)\s*[:：]/i.test(text.trim());
}

export function ScriptViewer({ content, className }: ScriptViewerProps) {
  const lines = content.split("\n");

  return (
    <div className={cn("space-y-1 font-mono text-sm leading-relaxed", className)}>
      {lines.map((line, idx) => {
        if (!line.trim()) {
          return <div key={idx} className="h-3" />;
        }

        const { isHeading, text } = parseSection(line);

        if (isHeading) {
          return (
            <h4
              key={idx}
              className="text-neon font-semibold text-base mt-4 mb-1 first:mt-0"
            >
              {text}
            </h4>
          );
        }

        if (isTimestamp(line)) {
          return (
            <p key={idx} className="text-amber-400/80 text-xs font-medium">
              {line}
            </p>
          );
        }

        if (isVoiceover(line)) {
          return (
            <p key={idx} className="text-foreground/90 italic border-l-2 border-neon/40 pl-3">
              {line}
            </p>
          );
        }

        return (
          <p key={idx} className="text-muted-foreground">
            {line}
          </p>
        );
      })}
    </div>
  );
}

export default ScriptViewer;
