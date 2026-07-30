"use client";

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";

export type Health = {
  status: string;
  version: string;
  environment: string;
};

/** The query key is shared between the query and every invalidation of it. */
export const healthKey = ["health"] as const;

export function useHealth() {
  return useQuery({
    queryKey: healthKey,
    queryFn: () => apiGet<Health>("/api/v1/health"),
  });
}
