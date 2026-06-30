import type { ToastItem, AlertKind } from "../types";

const kindStyle: Record<AlertKind, string> = {
  crowd: "border-red-500 bg-red-950/80",
  loitering: "border-amber-500 bg-amber-950/80",
  fire: "border-red-600 bg-red-950/90",
};

const kindIcon: Record<AlertKind, string> = {
  crowd: "👥",
  loitering: "🚶",
  fire: "🔥",
};

export default function AlertToast({ toasts }: { toasts: ToastItem[] }) {
  return (
    <div className="fixed top-16 right-4 z-50 flex flex-col gap-2 w-80">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`border rounded-lg px-4 py-3 shadow-lg backdrop-blur ${kindStyle[t.kind]}`}
        >
          <div className="flex items-center gap-2">
            <span className="text-lg">{kindIcon[t.kind]}</span>
            <div className="min-w-0">
              <div className="text-sm font-semibold text-gray-100">{t.message}</div>
              <div className="text-xs text-gray-400">{t.camera}</div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
