import { useState } from "react";
import type {
  CameraConfig,
  BenchmarkResponse,
  BenchmarkModelResult,
  Bbox,
} from "../types";
import { runBenchmark } from "../lib/api";

function ResultOverlay({
  result,
  width,
  height,
}: {
  result: BenchmarkModelResult;
  width: number;
  height: number;
}) {
  const members = new Set(result.cluster_member_indices ?? []);
  return (
    <svg
      className="absolute inset-0 w-full h-full pointer-events-none"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="xMidYMid slice"
    >
      {result.detections.map((d, i) => {
        const [x1, y1, x2, y2] = d.bbox;
        return (
          <rect
            key={i}
            x={x1}
            y={y1}
            width={x2 - x1}
            height={y2 - y1}
            fill="none"
            stroke={members.has(i) ? "#22c55e" : "#3b82f6"}
            strokeWidth={2}
          />
        );
      })}
      {result.cluster_bbox &&
        (() => {
          const [x1, y1, x2, y2] = result.cluster_bbox as Bbox;
          return (
            <rect
              x={x1}
              y={y1}
              width={x2 - x1}
              height={y2 - y1}
              fill="none"
              stroke="#ef4444"
              strokeWidth={3}
              strokeDasharray="10 6"
            />
          );
        })()}
    </svg>
  );
}

export default function BenchmarkPanel({ cameras }: { cameras: CameraConfig[] }) {
  const [camera, setCamera] = useState(cameras[0]?.name ?? "");
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<BenchmarkResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await runBenchmark(camera || undefined));
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-4">
      <div className="flex items-center gap-3 mb-4">
        <select
          value={camera}
          onChange={(e) => setCamera(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm"
        >
          {cameras.map((c) => (
            <option key={c.name} value={c.name}>
              {c.name}
            </option>
          ))}
        </select>
        <button
          onClick={run}
          disabled={loading}
          className="px-4 py-1.5 rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-sm font-medium"
        >
          {loading ? "Running…" : "Run Benchmark"}
        </button>
        {error && <span className="text-sm text-red-400">{error}</span>}
      </div>

      {data && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {data.results.map((r) => (
            <div key={r.model} className="border border-gray-800 rounded-lg overflow-hidden">
              <div className="px-3 py-2 bg-gray-900 flex items-center justify-between">
                <span className="font-semibold text-sm">{r.model}</span>
                {r.error ? (
                  <span className="text-xs text-red-400">{r.error}</span>
                ) : (
                  <span className="text-xs text-gray-400">
                    {r.latency_ms} ms · {r.person_count} persons · cluster {r.max_cluster_size}
                  </span>
                )}
              </div>
              <div className="relative bg-black aspect-video">
                <img
                  src={`data:image/jpeg;base64,${data.frame_b64}`}
                  alt={r.model}
                  className="w-full h-full object-cover"
                />
                {!r.error && (
                  <ResultOverlay result={r} width={data.frame_width} height={data.frame_height} />
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
