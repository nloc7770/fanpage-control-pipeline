// Typed REST client for the Facebook Reels module (Phase 2A/B/C backend).
import type {
  FacebookAccountDTO,
  FacebookPageDTO,
  ContentSourceDTO,
  ReelDraftDTO,
} from "@factory/shared-types";

import { ApiError } from "./api";

function apiBase(): string {
  const url = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";
  return url.replace(/\/+$/, "");
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("content-type")) {
    headers.set("content-type", "application/json");
  }
  headers.set("accept", "application/json");
  const res = await fetch(`${apiBase()}${path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
  if (!res.ok) {
    let code: string | undefined;
    let message = `${res.status} ${res.statusText}`;
    let details: Record<string, unknown> | undefined;
    try {
      const body = await res.json();
      if (body?.error) {
        code = body.error.code;
        if (body.error.message) message = body.error.message;
        details = body.error.details;
      }
    } catch {
      // ignore
    }
    throw new ApiError(res.status, message, code, details);
  }
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

function qs(params: object): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params as Record<string, unknown>)) {
    if (v === null || v === undefined || v === "") continue;
    parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  }
  return parts.length ? `?${parts.join("&")}` : "";
}

// ---- Facebook accounts ----------------------------------------------------

export interface ListFacebookAccountsResponse {
  accounts: FacebookAccountDTO[];
}
export interface ListFacebookPagesResponse {
  pages: FacebookPageDTO[];
}

export function listFacebookAccounts() {
  return request<ListFacebookAccountsResponse>("/facebook/accounts");
}
export function syncFacebookAccount(id: string) {
  return request<ListFacebookPagesResponse>(`/facebook/accounts/${id}/sync`, { method: "POST" });
}

// ---- Facebook pages -------------------------------------------------------

export interface FacebookPagesFilters {
  account_id?: string;
  status?: string;
}

export function listFacebookPages(filters: FacebookPagesFilters = {}) {
  return request<ListFacebookPagesResponse>(`/facebook/pages${qs(filters)}`);
}
export function getFacebookPage(id: string) {
  return request<FacebookPageDTO>(`/facebook/pages/${id}`);
}

export interface CreatePageManualBody {
  page_access_token: string;
  niche: string;
  language?: "vi" | "en";
  content_keywords?: string[];
  blocked_keywords?: string[];
  daily_reel_target?: number;
  posting_time_slots?: Array<{ day_of_week: number; hour: number; minute: number }>;
  auto_generate_enabled?: boolean;
}

export function createPageManual(body: CreatePageManualBody) {
  return request<FacebookPageDTO>("/facebook/pages", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteFacebookPage(id: string) {
  return request<{ deleted: boolean; page_id: string; facebook_page_id: string }>(
    `/facebook/pages/${id}`,
    { method: "DELETE" },
  );
}

export interface UpdateFacebookPageBody {
  niche?: string;
  language?: "vi" | "en";
  content_keywords?: string[];
  blocked_keywords?: string[];
  daily_reel_target?: number;
  posting_time_slots?: Array<{ day_of_week: number; hour: number; minute: number }>;
  auto_generate_enabled?: boolean;
}

export function updateFacebookPage(id: string, body: UpdateFacebookPageBody) {
  return request<FacebookPageDTO>(`/facebook/pages/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}
export function disableFacebookPage(id: string) {
  return request<FacebookPageDTO>(`/facebook/pages/${id}/disable`, { method: "POST" });
}
export function enableFacebookPage(id: string) {
  return request<FacebookPageDTO>(`/facebook/pages/${id}/enable`, { method: "POST" });
}
export function testFacebookPageToken(id: string) {
  return request<{ ok: boolean; status: string }>(`/facebook/pages/${id}/test-token`, { method: "POST" });
}
export function discoverForPage(id: string) {
  return request<{ discovered: number }>(`/facebook/pages/${id}/discover`, { method: "POST" });
}

// ---- Content sources ------------------------------------------------------

export interface ContentSourcesFilters {
  page_id?: string;
  status?: string;
  limit?: number;
  offset?: number;
}
export interface ListContentSourcesResponse {
  sources: ContentSourceDTO[];
  total: number;
}

export function listContentSources(filters: ContentSourcesFilters = {}) {
  return request<ListContentSourcesResponse>(`/content-sources${qs(filters)}`);
}
export function getContentSource(id: string) {
  return request<ContentSourceDTO>(`/content-sources/${id}`);
}
export function rejectContentSource(id: string, reason?: string) {
  return request<ContentSourceDTO>(`/content-sources/${id}/reject`, {
    method: "POST",
    body: JSON.stringify(reason ? { reason } : {}),
  });
}
export function queueContentSource(id: string) {
  return request<ContentSourceDTO>(`/content-sources/${id}/queue`, { method: "POST" });
}
export interface DeleteContentSourceResponse {
  source_id: string;
  deleted_jobs: number;
  deleted_reel_drafts: number;
  deleted_publish_jobs: number;
  deleted_files: number;
  freed_bytes: number;
}
export function deleteContentSource(id: string) {
  return request<DeleteContentSourceResponse>(`/content-sources/${id}`, { method: "DELETE" });
}

// ---- Reel drafts ----------------------------------------------------------

export interface ReelDraftsFilters {
  page_id?: string;
  approval_status?: string;
  publish_status?: string;
  from_date?: string;
  to_date?: string;
  limit?: number;
  offset?: number;
}
export interface ListReelDraftsResponse {
  drafts: ReelDraftDTO[];
  total: number;
}

export function listReelDrafts(filters: ReelDraftsFilters = {}) {
  return request<ListReelDraftsResponse>(`/reel-drafts${qs(filters)}`);
}
export function getReelDraft(id: string) {
  return request<ReelDraftDTO>(`/reel-drafts/${id}`);
}
export interface UpdateReelDraftBody {
  title?: string;
  caption?: string;
  hashtags?: string[];
  scheduled_at?: string | null;
}
export function updateReelDraft(id: string, body: UpdateReelDraftBody) {
  return request<ReelDraftDTO>(`/reel-drafts/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}
export interface ApproveReelBody {
  publish_now?: boolean;
  scheduled_at?: string;
}
export function approveReelDraft(id: string, body: ApproveReelBody = {}) {
  return request<ReelDraftDTO>(`/reel-drafts/${id}/approve`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
export function rejectReelDraft(id: string, reason?: string) {
  return request<ReelDraftDTO>(`/reel-drafts/${id}/reject`, {
    method: "POST",
    body: JSON.stringify(reason ? { reason } : {}),
  });
}
export function regenerateReelCaption(id: string) {
  return request<ReelDraftDTO>(`/reel-drafts/${id}/regenerate-caption`, { method: "POST" });
}
export function scheduleReelDraft(id: string, scheduled_at: string) {
  return request<ReelDraftDTO>(`/reel-drafts/${id}/schedule`, {
    method: "POST",
    body: JSON.stringify({ scheduled_at }),
  });
}
export function cancelReelSchedule(id: string) {
  return request<ReelDraftDTO>(`/reel-drafts/${id}/cancel-schedule`, { method: "POST" });
}

// ---- Asset URLs (existing API) -------------------------------------------

export function assetDownloadUrl(assetId: string | null | undefined): string | null {
  if (!assetId) return null;
  return `${apiBase()}/assets/${assetId}/download`;
}

export function facebookLoginUrl(): string {
  return `${apiBase()}/auth/facebook/login`;
}
