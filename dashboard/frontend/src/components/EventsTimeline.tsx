import type { TimelineEntry, AlertKind } from "../types";

const kindStyle: Record<AlertKind, string> = {
  crowd: "text-red-400",
  loitering: "text-amber-400",
  fire: "text-orange-400",
};

const kindIcon: Record<AlertKind, string> = {
  crowd: "👥",
  loitering: "🚶",
  fire: "🔥",
};

function fmt(ts: number): string {
  return new Date(ts).toLocaleTimeString();
}

export default function EventsTimeline({ entries }: { entries: TimelineEntry[] }) {
  return (
    <div className="h-full flex flex-col bg-gray-900/40 border-l border-gray-800">
      <div className="px-4 py-3 border-b border-gray-800 text-sm font-semibold text-gray-300">
        Live Events
      </div>
      <div className="flex-1 overflow-y-auto">
        {entries.length === 0 && (
          <div className="p-4 text-xs text-gray-600">No events yet.</div>
        )}
        {entries.map((e) => (
          <div
            key={e.id}
            className="px-4 py-2 border-b border-gray-800/60 flex items-start gap-2"
          >
            <span>{kindIcon[e.kind]}</span>
            <div className="flex-1 min-w-0">
              <div className={`text-sm font-medium ${kindStyle[e.kind]}`}>{e.text}</div>
              <div className="text-xs text-gray-500">
                {e.camera} · {fmt(e.ts)}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
