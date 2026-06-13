"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Home, FileText, Send, BookOpen } from "lucide-react";
import { cn } from "@/lib/cn";

const tabs = [
  { href: "/", label: "Trang chủ", icon: Home },
  { href: "/content", label: "Nội dung", icon: FileText },
  { href: "/publish", label: "Đăng", icon: Send },
  { href: "/scripts", label: "Kịch bản", icon: BookOpen },
];

export function MobileNav() {
  const pathname = usePathname();

  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-50 border-t border-border/60 bg-background/95 backdrop-blur-md md:hidden"
      aria-label="Mobile navigation"
    >
      <div className="flex h-16 items-center justify-around">
        {tabs.map((tab) => {
          const isActive = pathname === tab.href || (tab.href !== "/" && pathname.startsWith(tab.href));
          const Icon = tab.icon;

          return (
            <Link
              key={tab.href}
              href={tab.href}
              className={cn(
                "flex flex-col items-center gap-1 px-3 py-2 text-xs font-medium transition-colors",
                isActive ? "text-neon" : "text-muted-foreground",
              )}
            >
              <div className="relative">
                <Icon className={cn("h-5 w-5", isActive && "drop-shadow-[0_0_6px_rgba(57,255,20,0.6)]")} />
                {isActive && (
                  <span className="absolute -top-1 -right-1 h-1.5 w-1.5 rounded-full bg-neon shadow-glow" />
                )}
              </div>
              <span>{tab.label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}

export default MobileNav;
