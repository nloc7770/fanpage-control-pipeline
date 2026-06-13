"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useThemeStore } from "@/stores/theme-store";
import { cn } from "@/lib/cn";

interface HealthCheck {
  ok: boolean;
}

async function fetchHealth(): Promise<HealthCheck> {
  const base = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080").replace(/\/+$/, "");
  const res = await fetch(`${base}/healthz`, { cache: "no-store" });
  if (!res.ok) throw new Error(`healthz ${res.status}`);
  return { ok: true };
}

function StatusPill() {
  const { data, isError, isLoading } = useQuery<HealthCheck>({
    queryKey: ["healthz"],
    queryFn: fetchHealth,
    refetchInterval: 30_000,
    refetchOnWindowFocus: false,
    retry: 1,
    staleTime: 25_000,
  });

  const online = !!data && !isError;
  const label = isLoading ? "Checking…" : online ? "API online" : "API offline";

  return (
    <div
      className="hidden items-center gap-2 rounded-full border border-border/60 bg-background/40 px-2.5 py-1 text-xs text-muted-foreground sm:inline-flex"
      role="status"
      aria-live="polite"
    >
      <span className="relative inline-flex h-2 w-2">
        <span
          className={cn(
            "absolute inline-flex h-full w-full rounded-full opacity-60",
            online ? "bg-neon animate-soft-pulse" : "bg-rose-500",
          )}
        />
        <span
          className={cn(
            "relative inline-flex h-2 w-2 rounded-full",
            online ? "bg-neon" : "bg-rose-400",
          )}
        />
      </span>
      <span className="tracking-tight">{label}</span>
    </div>
  );
}

const navLinks = [
  { href: "/", label: "Trang chủ" },
  { href: "/content", label: "Nội dung" },
  { href: "/publish", label: "Lịch đăng" },
  { href: "/scripts", label: "Kịch bản" },
];

export function Topbar() {
  const pathname = usePathname();
  const theme = useThemeStore((s) => s.theme);
  const toggle = useThemeStore((s) => s.toggle);
  const hydrate = useThemeStore((s) => s.hydrate);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  return (
    <header className="sticky top-0 z-40 w-full border-b border-border/60 bg-background/80 backdrop-blur-md supports-[backdrop-filter]:bg-background/60">
      <div className="container flex h-14 items-center justify-between gap-3">
        <Link
          href="/"
          className="flex items-center gap-2 font-semibold tracking-tight focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-sm"
        >
          <span
            aria-hidden
            className="grid h-7 w-7 place-items-center rounded-md bg-neon/10 border border-neon/30 text-[10px] font-bold uppercase text-neon shadow-glow"
          >
            SD
          </span>
          <span className="text-sm tracking-tight">
            Skinny <span className="text-neon font-bold">Dad</span>
          </span>
        </Link>

        <nav className="hidden md:flex items-center gap-1">
          {navLinks.map((link) => {
            const isActive = pathname === link.href || (link.href !== "/" && pathname.startsWith(link.href));
            return (
              <Link
                key={link.href}
                href={link.href}
                className={cn(
                  "relative px-3 py-1.5 text-sm font-medium transition-colors rounded-md hover:text-foreground",
                  isActive ? "text-neon" : "text-muted-foreground",
                )}
              >
                {link.label}
                {isActive && (
                  <span className="absolute bottom-0 left-1/2 -translate-x-1/2 h-0.5 w-4/5 rounded-full bg-neon shadow-glow" />
                )}
              </Link>
            );
          })}
        </nav>

        <div className="flex items-center gap-1.5">
          <StatusPill />
          <Button variant="ghost" size="icon" aria-label="Toggle theme" onClick={toggle}>
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
        </div>
      </div>
    </header>
  );
}

export default Topbar;
