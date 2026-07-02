import type { TimelineEntry, AlertKind } from "../types";

const kindStyle: Record<AlertKind, string> = {
  crowd: "text-red-400",
  loitering: "text-amber-400",
  fire: "text-orange-400",
  lpr: "text-cyan-400",
  stopped_vehicle: "text-amber-500",
  no_helmet: "text-red-500",
};

const kindIcon: Record<AlertKind, string> = {
  crowd: "👥",
  loitering: "🚶",
  fire: "🔥",
  lpr: "LP",
  stopped_vehicle: "🛑",
  no_helmet: "🪖",
};

function fmt(ts: number): string {
  return new Date(ts).toLocaleTimeString();
}

function pct(v?: number): string {
  return v === undefined || v === null ? "—" : `${Math.round(v * 100)}%`;
}

function ImageEventRow({ e }: { e: TimelineEntry }) {
  const isLpr = e.kind === "lpr";
  const titleColor = isLpr ? "text-cyan-400" : (e.kind === "no_helmet" ? "text-red-400" : "text-amber-400");
  const borderColor = isLpr ? "border-cyan-700/60" : (e.kind === "no_helmet" ? "border-red-700/60" : "border-amber-700/60");
  const titleText = isLpr ? "LP · LICENSE PLATE" : (e.kind === "no_helmet" ? "🪖 · NO HELMET" : "🛑 · STOPPED VEHICLE");

  return (
    <div className="px-4 py-2.5 border-b border-gray-800/60">
      <div className="flex items-center gap-1.5 mb-1.5">
        <span className={`${titleColor} text-xs font-semibold tracking-wide`}>{titleText}</span>
        <span className="text-xs text-gray-500 ml-auto shrink-0">
          {e.camera} · {fmt(e.ts)}
        </span>
      </div>
      <div className="flex items-center gap-3">
        {e.imageUrl ? (
          <img
            src={e.imageUrl}
            alt={e.plateText || "crop"}
            className={`h-10 w-auto max-w-[130px] rounded border ${borderColor} object-contain bg-black shrink-0`}
          />
        ) : (
          <div className="h-10 w-20 rounded border border-gray-700 grid place-items-center text-[10px] text-gray-600 shrink-0">
            no img
          </div>
        )}
        <div className="min-w-0">
          <div className={`font-mono text-base font-bold tracking-widest ${titleColor} truncate`}>
            {e.plateText || e.text}
          </div>
          <div className="text-[11px] text-gray-400 flex gap-2 flex-wrap">
            {e.detConf !== undefined && <span>det {pct(e.detConf)}</span>}
            {e.ocrConf !== undefined && <span>ocr {pct(e.ocrConf)}</span>}
            {e.conf !== undefined && <span className="text-gray-200">conf {pct(e.conf)}</span>}
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
          e.kind === "lpr" || e.kind === "no_helmet" || (e.kind === "stopped_vehicle" && e.imageUrl) ? (
            <ImageEventRow key={e.id} e={e} />
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
