import type { CameraConfig } from "../types";

interface Props {
  connected: boolean;
  cameras: CameraConfig[];
  activeTab: "live" | "benchmark";
  onTabChange: (tab: "live" | "benchmark") => void;
}

export default function Header({ connected, cameras, activeTab, onTabChange }: Props) {
  const tabBtn = (id: "live" | "benchmark", label: string) => (
    <button
      onClick={() => onTabChange(id)}
      className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
        activeTab === id
          ? "bg-blue-600 text-white"
          : "text-gray-400 hover:text-gray-200 hover:bg-gray-800"
      }`}
    >
      {label}
    </button>
  );

  return (
    <header className="flex items-center justify-between px-5 py-3 border-b border-gray-800 bg-gray-900/60">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold tracking-tight">🏙️ Smart City Dashboard</h1>
        <span className="text-xs text-gray-500">{cameras.length} cameras</span>
      </div>
      <div className="flex items-center gap-2">
        {tabBtn("live", "Live")}
        {tabBtn("benchmark", "Benchmark")}
      </div>
      <div className="flex items-center gap-2 text-sm">
        <span
          className={`inline-block w-2 h-2 rounded-full ${
            connected ? "bg-green-500" : "bg-red-500"
          }`}
        />
        <span className={connected ? "text-green-400" : "text-red-400"}>
          {connected ? "Connected" : "Disconnected"}
        </span>
      </div>
    </header>
  );
}
