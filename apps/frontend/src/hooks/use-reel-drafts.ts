"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listReelDrafts,
  getReelDraft,
  updateReelDraft,
  approveReelDraft,
  scheduleReelDraft,
  cancelReelSchedule,
  type ReelDraftsFilters,
  type UpdateReelDraftBody,
  type ApproveReelBody,
} from "@/lib/api-fb";
import type { ReelDraftDTO } from "@factory/shared-types";

const IN_FLIGHT_PUBLISH = new Set(["scheduled", "publishing"]);
const IN_FLIGHT_APPROVAL = new Set(["pending"]);

function hasInFlight(items: ReelDraftDTO[] | undefined) {
  return !!items?.some(
    (d) =>
      IN_FLIGHT_APPROVAL.has(d.approval_status) || IN_FLIGHT_PUBLISH.has(d.publish_status),
  );
}

/**
 * List reel drafts with optional filters.
 * No page_id required — fetches across all pages.
 */
export function useReelDrafts(filters: ReelDraftsFilters = {}) {
  return useQuery({
    queryKey: ["reel-drafts", filters],
    queryFn: () => listReelDrafts(filters),
    staleTime: 5_000,
    refetchInterval: (query) => (hasInFlight(query.state.data?.drafts) ? 5_000 : false),
    refetchOnWindowFocus: true,
  });
}

/**
 * Single reel draft by ID.
 */
export function useReelDraft(id: string | undefined) {
  return useQuery({
    queryKey: ["reel-draft", id],
    queryFn: () => getReelDraft(id as string),
    enabled: !!id,
    staleTime: 3_000,
    refetchInterval: (query) => {
      const d = query.state.data;
      if (!d) return false;
      if (IN_FLIGHT_PUBLISH.has(d.publish_status) || d.approval_status === "pending") return 3_000;
      return false;
    },
    refetchOnWindowFocus: true,
  });
}

/**
 * Mutation: update a reel draft (title, caption, hashtags).
 */
export function useUpdateReelDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateReelDraftBody }) =>
      updateReelDraft(id, body),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ["reel-draft", variables.id] });
      qc.invalidateQueries({ queryKey: ["reel-drafts"] });
    },
  });
}

/**
 * Mutation: approve a reel draft (optionally schedule or publish now).
 */
export function useApproveReelDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body?: ApproveReelBody }) =>
      approveReelDraft(id, body ?? {}),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ["reel-draft", variables.id] });
      qc.invalidateQueries({ queryKey: ["reel-drafts"] });
    },
  });
}

/**
 * Mutation: schedule a reel draft for a specific time.
 */
export function useScheduleReelDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, scheduled_at }: { id: string; scheduled_at: string }) =>
      scheduleReelDraft(id, scheduled_at),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ["reel-draft", variables.id] });
      qc.invalidateQueries({ queryKey: ["reel-drafts"] });
    },
  });
}

/**
 * Mutation: cancel a scheduled reel draft.
 */
export function useCancelSchedule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => cancelReelSchedule(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: ["reel-draft", id] });
      qc.invalidateQueries({ queryKey: ["reel-drafts"] });
    },
  });
}
