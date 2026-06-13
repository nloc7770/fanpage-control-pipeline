"use client";

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ToastRoot } from "@/components/ui/toast";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: (failureCount, error) => {
              if (failureCount >= 2) return false;
              const status = (error as { status?: number } | null)?.status;
              if (typeof status === "number" && status >= 400 && status < 500) return false;
              return true;
            },
            refetchOnWindowFocus: false,
            staleTime: 5_000,
          },
          mutations: { retry: false },
        },
      }),
  );

  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delayDuration={150}>
        <ToastRoot>{children}</ToastRoot>
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default Providers;
