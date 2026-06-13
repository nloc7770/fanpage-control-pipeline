import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { Topbar } from "@/components/topbar";
import { MobileNav } from "@/components/mobile-nav";

export const metadata: Metadata = {
  title: "Skinny Dad | Dev & Gym",
  description: "Quản lý fanpage cá nhân - Gym transformation journey",
};

export const viewport: Viewport = {
  themeColor: "#39ff14",
  width: "device-width",
  initialScale: 1,
};

// Inline script to apply persisted theme before first paint to avoid FOUC.
const themeBootstrap = `
(function(){try{var t=localStorage.getItem('factory.theme');var dark=t?t==='dark':true;var c=document.documentElement.classList;dark?c.add('dark'):c.remove('dark');}catch(e){document.documentElement.classList.add('dark');}})();
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi" className="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootstrap }} />
      </head>
      <body className="dashboard min-h-screen bg-background">
        <Providers>
          <div className="flex min-h-dvh flex-col">
            <Topbar />
            <main className="container flex-1 py-6 pb-20 md:py-10 md:pb-10">{children}</main>
            <footer className="hidden md:block border-t border-border/60">
              <div className="container flex h-10 items-center justify-between text-xs text-muted-foreground">
                <span>&copy; 2026 Skinny Dad &mdash; Dev & Gym</span>
                <span className="hidden sm:inline">Gym transformation journey</span>
              </div>
            </footer>
            <MobileNav />
          </div>
        </Providers>
      </body>
    </html>
  );
}
