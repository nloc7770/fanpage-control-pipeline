"use client";

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";

export type ScriptStatus = "unfilmed" | "filmed" | "published";

export interface ScriptDTO {
  slug: string;
  title: string;
  status: ScriptStatus;
  duration_seconds: number;
  hook: string;
  content: string;
  caption: string;
  hashtags: string[];
  created_at: string;
  updated_at: string;
}

export interface ListScriptsResponse {
  scripts: ScriptDTO[];
  total: number;
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

// --- API functions ---

function listScripts() {
  return request<ListScriptsResponse>("/scripts");
}

function getScript(slug: string) {
  return request<ScriptDTO>(`/scripts/${encodeURIComponent(slug)}`);
}

function updateScriptStatus(slug: string, status: ScriptStatus) {
  return request<ScriptDTO>(`/scripts/${encodeURIComponent(slug)}`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

interface CreateDraftFromScriptBody {
  title: string;
  caption: string;
  hashtags: string[];
}

function createDraftFromScript(slug: string, body: CreateDraftFromScriptBody) {
  return request<{ draft_id: string }>(`/scripts/${encodeURIComponent(slug)}/create-draft`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// --- Hooks ---

export function useScripts() {
  return useQuery({
    queryKey: ["scripts"],
    queryFn: listScripts,
    staleTime: 10_000,
    refetchOnWindowFocus: true,
  });
}

export function useScript(slug: string | undefined) {
  return useQuery({
    queryKey: ["script", slug],
    queryFn: () => getScript(slug as string),
    enabled: !!slug,
    staleTime: 10_000,
  });
}

export function useUpdateScriptStatus() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ slug, status }: { slug: string; status: ScriptStatus }) =>
      updateScriptStatus(slug, status),
    onSuccess: (_data, { slug }) => {
      queryClient.invalidateQueries({ queryKey: ["scripts"] });
      queryClient.invalidateQueries({ queryKey: ["script", slug] });
    },
  });
}

export function useCreateDraftFromScript() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ slug, body }: { slug: string; body: CreateDraftFromScriptBody }) =>
      createDraftFromScript(slug, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["reel-drafts"] });
    },
  });
}
