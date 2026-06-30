import type { DashboardConfig, BenchmarkResponse } from "../types";

export async function fetchConfig(): Promise<DashboardConfig> {
  const r = await fetch("/dashboard/config");
  if (!r.ok) throw new Error("Failed to fetch config");
  return r.json();
}

export async function runBenchmark(camera?: string): Promise<BenchmarkResponse> {
  const r = await fetch("/benchmark/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ camera: camera ?? null }),
  });
  if (!r.ok) throw new Error("Failed to run benchmark");
  return r.json();
}

export function snapshotUrl(camera: string, h = 360): string {
  return `/api/${camera}/latest.jpg?h=${h}&t=${Date.now()}`;
}

export function captureUrl(camera: string): string {
  return `/api/${camera}/latest.jpg?t=${Date.now()}`;
}
