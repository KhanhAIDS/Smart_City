import type { TimelineEntry, AlertKind } from "../types";

const kindStyle: Record<AlertKind, string> = {
  crowd: "text-red-400",
  loitering: "text-amber-400",
  fire: "text-orange-400",
  lpr: "text-cyan-400",
};

const kindIcon: Record<AlertKind, string> = {
  crowd: "👥",
  loitering: "🚶",
  fire: "🔥",
  lpr: "LP",
};

function fmt(ts: number): string {
  return new Date(ts).toLocaleTimeString();
}

function pct(v?: number): string {
  return v === undefined || v === null ? "—" : `${Math.round(v * 100)}%`;
}

function LprEventRow({ e }: { e: TimelineEntry }) {
  return (
    <div className="px-4 py-2.5 border-b border-gray-800/60">
      <div className="flex items-center gap-1.5 mb-1.5">
        <span className="text-cyan-400 text-xs font-semibold tracking-wide">LP · LICENSE PLATE</span>
        <span className="text-xs text-gray-500 ml-auto shrink-0">
          {e.camera} · {fmt(e.ts)}
        </span>
      </div>
      <div className="flex items-center gap-3">
        {e.imageUrl ? (
          <img
            src={e.imageUrl}
            alt={e.plateText || "plate"}
            className="h-10 w-auto max-w-[130px] rounded border border-cyan-700/60 object-contain bg-black shrink-0"
          />
        ) : (
          <div className="h-10 w-20 rounded border border-gray-700 grid place-items-center text-[10px] text-gray-600 shrink-0">
            no img
          </div>
        )}
        <div className="min-w-0">
          <div className="font-mono text-base font-bold tracking-widest text-cyan-300 truncate">
            {e.plateText || "—"}
          </div>
          <div className="text-[11px] text-gray-400 flex gap-2 flex-wrap">
            <span>det {pct(e.detConf)}</span>
            <span>ocr {pct(e.ocrConf)}</span>
            <span className="text-gray-200">conf {pct(e.conf)}</span>
          </div>
        </div>
      </div>
    </div>
  );
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
        {entries.map((e) =>
          e.kind === "lpr" ? (
            <LprEventRow key={e.id} e={e} />
          ) : (
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
          )
        )}
      </div>
    </div>
  );
}
