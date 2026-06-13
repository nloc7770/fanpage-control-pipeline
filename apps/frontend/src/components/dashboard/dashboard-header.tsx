"use client";

import { LABELS, VIETNAMESE_DAYS, VIETNAMESE_MONTHS, APP_NAME } from "@/lib/constants";

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return LABELS.greeting.morning;
  if (hour < 18) return LABELS.greeting.afternoon;
  return LABELS.greeting.evening;
}

function getVietnameseDate(): string {
  const now = new Date();
  const day = VIETNAMESE_DAYS[now.getDay()];
  const date = now.getDate();
  const month = VIETNAMESE_MONTHS[now.getMonth()];
  const year = now.getFullYear();
  return `${day}, ${date} ${month} ${year}`;
}

export function DashboardHeader() {
  const greeting = getGreeting();
  const dateStr = getVietnameseDate();

  return (
    <header className="mb-8">
      <div className="flex flex-col gap-1">
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">
          {greeting}{" "}
          <span className="inline-block" role="img" aria-label="fist bump">
            \ud83d\udc4a
          </span>
        </h1>
        <p className="text-sm text-muted-foreground">{dateStr}</p>
        <p className="text-xs text-muted-foreground/70">{APP_NAME}</p>
      </div>
    </header>
  );
}

export default DashboardHeader;
