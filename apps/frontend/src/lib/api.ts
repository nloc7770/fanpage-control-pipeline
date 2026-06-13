// Typed REST client. Browser-side only (uses NEXT_PUBLIC_API_URL). All
// non-2xx responses throw ApiError so TanStack Query can route them to the
// error path uniformly.

import type {
  ClipDTO,
  CreateJobRequest,
  JobDTO,
  JobStatus,
  ListClipsResponse,
  ListJobsResponse,
} from "@factory/shared-types";

export class ApiError extends Error {
  status: number;
  code: string | undefined;
  details: Record<string, unknown> | undefined;

  constructor(status: number, message: string, code?: string, details?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

interface ApiErrorBody {
  error?: { code?: string; message?: string; details?: Record<string, unknown> };
}

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
      const body = (await res.json()) as ApiErrorBody;
      if (body.error) {
        code = body.error.code;
        if (body.error.message) message = body.error.message;
        details = body.error.details;
      }
    } catch {
      // body was not JSON; keep status-based message
    }
    throw new ApiError(res.status, message, code, details);
  }

  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

// ---- jobs -----------------------------------------------------------------

export function createJob(body: CreateJobRequest): Promise<JobDTO> {
  return request<JobDTO>("/jobs", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export interface ListJobsParams {
  limit?: number;
  offset?: number;
  status?: JobStatus[];
}

export function listJobs(params: ListJobsParams = {}): Promise<ListJobsResponse> {
  const search = new URLSearchParams();
  if (params.limit != null) search.set("limit", String(params.limit));
  if (params.offset != null) search.set("offset", String(params.offset));
  if (params.status) {
    for (const s of params.status) search.append("status", s);
  }
  const qs = search.toString();
  return request<ListJobsResponse>(`/jobs${qs ? `?${qs}` : ""}`);
}

export function getJob(id: string): Promise<JobDTO> {
  return request<JobDTO>(`/jobs/${encodeURIComponent(id)}`);
}

export function getJobClips(id: string): Promise<ListClipsResponse> {
  return request<ListClipsResponse>(`/jobs/${encodeURIComponent(id)}/clips`);
}

// ---- logs -----------------------------------------------------------------
// `GET /jobs/{id}/logs` returns structured pipeline log entries (chronological).
// Defined inline rather than in @factory/shared-types because the backend keeps
// LogDTO as a local pydantic model — we mirror its shape here.

export interface LogDTO {
  id: string;
  stage: string | null;
  level: string;
  message: string;
  payload: Record<string, unknown> | null;
  created_at: string;
}

export interface ListLogsResponse {
  logs: LogDTO[];
  total: number;
}

export interface GetJobLogsParams {
  limit?: number;
  offset?: number;
}

export function getJobLogs(id: string, params: GetJobLogsParams = {}): Promise<ListLogsResponse> {
  const search = new URLSearchParams();
  if (params.limit != null) search.set("limit", String(params.limit));
  if (params.offset != null) search.set("offset", String(params.offset));
  const qs = search.toString();
  return request<ListLogsResponse>(`/jobs/${encodeURIComponent(id)}/logs${qs ? `?${qs}` : ""}`);
}

// ---- clips ----------------------------------------------------------------
// Note: a `GET /clips/{id}` endpoint is not in docs/api-contracts.md. We
// derive single-clip lookups from `getJobClips` on the detail page. Keeping
// the placeholder typed in case the backend adds it later.

export async function getClip(jobId: string, clipId: string): Promise<ClipDTO | undefined> {
  const res = await getJobClips(jobId);
  return res.clips.find((c) => c.id === clipId);
}

// ---- assets ---------------------------------------------------------------

export function downloadAssetUrl(assetId: string): string {
  return `${apiBase()}/assets/${encodeURIComponent(assetId)}/download`;
}

export interface AssetRecord {
  id: string;
  job_id: string;
  kind: string;
  path: string;
  size_bytes: number | null;
  mime: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export function getAsset(assetId: string): Promise<AssetRecord> {
  return request<AssetRecord>(`/assets/${encodeURIComponent(assetId)}`);
}

// ---- artifacts -----------------------------------------------------------
// `GET /jobs/{id}/artifacts/{kind}` returns the parsed JSON content of one or
// more persisted artifact files for a given pipeline stage. Binary kinds
// (clip_video, etc.) respond with 415 and must be downloaded instead.
//
// Allowed `kind` values are also enforced server-side. We keep them as a union
// so misuses are caught by TS at the call site.

export type ArtifactKind =
  | "transcript_json"
  | "diarization_json"
  | "yolo_json"
  | "analysis_json"
  | "edit_plan_json";

export interface ArtifactFile<TContent = unknown> {
  filename: string;
  asset_id: string;
  size_bytes: number | null;
  created_at: string;
  content: TContent;
}

export interface ArtifactResponse<TContent = unknown> {
  job_id: string;
  kind: ArtifactKind;
  files: ArtifactFile<TContent>[];
}

export function getJobArtifact<TContent = unknown>(
  jobId: string,
  kind: ArtifactKind,
): Promise<ArtifactResponse<TContent>> {
  return request<ArtifactResponse<TContent>>(
    `/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(kind)}`,
  );
}

// ---- sse base url ---------------------------------------------------------

export function sseBaseUrl(): string {
  const url = process.env.NEXT_PUBLIC_SSE_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";
  return url.replace(/\/+$/, "");
}
