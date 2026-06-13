"use client";

import { useQuery } from "@tanstack/react-query";
import { listFacebookPages, getFacebookPage, type FacebookPagesFilters } from "@/lib/api-fb";
import type { FacebookPageDTO } from "@factory/shared-types";

const TERMINAL_STATUSES = new Set(["disabled", "token_expired", "permission_missing", "error"]);

function hasInFlight(pages: FacebookPageDTO[] | undefined): boolean {
  if (!pages) return false;
  return pages.some((p) => !TERMINAL_STATUSES.has(p.status));
}

export function useFacebookPages(filters: FacebookPagesFilters = {}) {
  return useQuery({
    queryKey: ["facebook-pages", filters],
    queryFn: () => listFacebookPages(filters),
    staleTime: 5_000,
    refetchInterval: (query) => (hasInFlight(query.state.data?.pages) ? 5_000 : false),
    refetchOnWindowFocus: true,
  });
}

export function useFacebookPage(id: string | undefined) {
  return useQuery({
    queryKey: ["facebook-page", id],
    queryFn: () => getFacebookPage(id as string),
    enabled: !!id,
    staleTime: 5_000,
    refetchOnWindowFocus: true,
  });
}
