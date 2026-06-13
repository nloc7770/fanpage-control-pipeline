"use client";

import Link from "next/link";
import { PenLine, CalendarRange, FileText } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LABELS } from "@/lib/constants";

interface QuickAction {
  label: string;
  href: string;
  icon: React.ReactNode;
  description: string;
}

const actions: QuickAction[] = [
  {
    label: LABELS.actions.createContent,
    href: "/scripts",
    icon: <PenLine className="h-5 w-5" />,
    description: "Viết kịch bản cho video mới",
  },
  {
    label: LABELS.actions.viewSchedule,
    href: "/publish",
    icon: <CalendarRange className="h-5 w-5" />,
    description: "Quản lý lịch đăng bài",
  },
  {
    label: LABELS.actions.manageDrafts,
    href: "/reel-drafts",
    icon: <FileText className="h-5 w-5" />,
    description: "Xem và duyệt bản nháp",
  },
];

export function QuickActions() {
  return (
    <Card className="border-border/50 bg-card/80">
      <CardHeader className="pb-3">
        <CardTitle className="text-base font-semibold">
          {LABELS.dashboard.quickActions}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid gap-2">
          {actions.map((action) => (
            <Link
              key={action.href}
              href={action.href}
              className="group flex items-center gap-3 rounded-lg border border-border/40 bg-background/50 p-3 transition-all hover:border-neon/30 hover:bg-neon/5 hover:shadow-glow/10"
            >
              <div className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-neon/10 text-neon transition-colors group-hover:bg-neon/20">
                {action.icon}
              </div>
              <div className="min-w-0">
                <p className="text-sm font-medium text-foreground group-hover:text-neon transition-colors">
                  {action.label}
                </p>
                <p className="text-xs text-muted-foreground">{action.description}</p>
              </div>
            </Link>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

export default QuickActions;
