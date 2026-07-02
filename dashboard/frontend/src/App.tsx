import { useEffect, useRef, useState } from "react";
import type {
  DashboardConfig,
  CrowdOverlayState,
  LoiterOverlayState,
  FireOverlayState,
  LprOverlayState,
  StoppedVehicleOverlayState,
  NoHelmetOverlayState,
  TimelineEntry,
  WsMessage,
} from "./types";
import { fetchConfig } from "./lib/api";
import { useLiveChannel } from "./lib/useLiveChannel";
import Header from "./components/Header";
import CameraGrid from "./components/CameraGrid";
import EventsTimeline from "./components/EventsTimeline";
import BenchmarkPanel from "./components/BenchmarkPanel";

const OVERLAY_TTL_MS = 1200;
const LPR_OVERLAY_TTL_MS = 2000;
const MAX_TIMELINE = 80;

function pruneStale<T extends { receivedAt: number }>(
  map: Record<string, T>,
  now: number,
  ttl: number
): Record<string, T> {
  let changed = false;
  const out: Record<string, T> = {};
  for (const k in map) {
    if (now - map[k].receivedAt <= ttl) out[k] = map[k];
    else changed = true;
  }
  return changed ? out : map;
}

export default function App() {
  const [config, setConfig] = useState<DashboardConfig | null>(null);
  const [tab, setTab] = useState<"live" | "benchmark">("live");
  const [crowdOverlays, setCrowdOverlays] = useState<Record<string, CrowdOverlayState>>({});
  const [loiters, setLoiters] = useState<Record<string, LoiterOverlayState>>({});
  const [fires, setFires] = useState<Record<string, FireOverlayState>>({});
  const [lprs, setLprs] = useState<Record<string, LprOverlayState>>({});
  const [stoppedVehicles, setStoppedVehicles] = useState<Record<string, StoppedVehicleOverlayState>>({});
  const [noHelmets, setNoHelmets] = useState<Record<string, NoHelmetOverlayState>>({});
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);

  const counter = useRef(0);
  const dimsRef = useRef<Record<string, { width: number; height: number }>>({});

  useEffect(() => {
    fetchConfig()
      .then((cfg) => {
        setConfig(cfg);
        const dims: Record<string, { width: number; height: number }> = {};
        for (const c of cfg.cameras) dims[c.name] = { width: c.width, height: c.height };
        dimsRef.current = dims;
      })
      .catch(console.error);
  }, []);

  useEffect(() => {
    const t = setInterval(() => {
      const now = Date.now();
      setCrowdOverlays((prev) => pruneStale(prev, now, OVERLAY_TTL_MS));
      setLoiters((prev) => pruneStale(prev, now, OVERLAY_TTL_MS));
      setFires((prev) => pruneStale(prev, now, OVERLAY_TTL_MS));
      setLprs((prev) => pruneStale(prev, now, LPR_OVERLAY_TTL_MS));
      setStoppedVehicles((prev) => pruneStale(prev, now, 1500));
      setNoHelmets((prev) => pruneStale(prev, now, 2000));
    }, 500);
    return () => clearInterval(t);
  }, []);

  const nextId = () => `${Date.now()}-${counter.current++}`;

  const pushTimeline = (e: Omit<TimelineEntry, "id">) => {
    setTimeline((prev) => [{ id: nextId(), ...e }, ...prev].slice(0, MAX_TIMELINE));
  };

  const handleMessage = (msg: WsMessage) => {
    const now = Date.now();

    if (msg.type === "realtime_objects") {
      const d = msg.data;
      setLoiters((prev) => {
        const loiter = prev[d.camera];
        if (!loiter || !loiter.active) return prev;
        
        const lObj = d.objects?.find((o: any) => o.age_seconds >= 40);
        if (lObj) {
           return {
             ...prev,
             [d.camera]: {
               ...loiter,
               bbox: lObj.bbox,
               dwellSeconds: lObj.age_seconds ?? 0,
               receivedAt: now
             }
           };
        }
        return prev;
      });
    } else if (msg.type === "realtime_crowd") {
      const d = msg.data;
      const personCount = d.person_count ?? d.clusters?.[0]?.size ?? 0;
      const threshold = d.threshold ?? 3;
      setCrowdOverlays((prev) => {
        const existing = prev[d.camera] || { detections: [], clusters: [], inferenceResolution: [1280, 720], personCount: 0, threshold };
        return {
          ...prev,
          [d.camera]: {
            ...existing,
            detections: d.detections || existing.detections,
            inferenceResolution: [d.width, d.height],
            clusters: d.clusters || [],
            clusterBbox: d.clusters?.[0]?.bbox || null,
            memberIndices: d.clusters?.[0]?.member_indices || [],
            personCount,
            threshold,
            receivedAt: now,
          },
        };
      });
    } else if (msg.type === "realtime_fire_smoke") {
      const d = msg.data;
      const detections = d.detections || [];
      const fireCount = d.fire_count || 0;
      const smokeCount = d.smoke_count || 0;
      if (detections.length === 0 && fireCount === 0 && smokeCount === 0) return;
      setFires((prev) => ({
        ...prev,
        [d.camera]: {
          detections,
          inferenceResolution: [d.width, d.height],
          fireCount,
          smokeCount,
          active: true,
          receivedAt: now,
        },
      }));
    } else if (msg.type === "realtime_lpr") {
      const d = msg.data;
      const plates = d.plates || [];
      if (plates.length === 0) return;
      setLprs((prev) => ({
        ...prev,
        [d.camera]: {
          plates,
          inferenceResolution: [d.width, d.height],
          receivedAt: now,
        },
      }));
    } else if (msg.type === "realtime_stopped_vehicle") {
      const d = msg.data;
      setStoppedVehicles((prev) => ({
        ...prev,
        [d.camera]: {
          vehicles: d.vehicles || [],
          receivedAt: now,
        },
      }));
    } else if (msg.type === "realtime_helmet") {
      const d = msg.data;
      setNoHelmets((prev) => ({
        ...prev,
        [d.camera]: {
          noHelmets: d.no_helmets || [],
          receivedAt: now,
        },
      }));
    } else if (msg.type === "realtime_alert") {
      const d = msg.data;
      if (d.person_count !== undefined) {
         if (d.active) {
             setCrowdOverlays((prev) => {
                const existing = prev[d.camera] || { detections: [], clusters: [], inferenceResolution: [1280, 720] };
                return {
                  ...prev,
                  [d.camera]: { ...existing, personCount: d.person_count || 0, threshold: 3, receivedAt: now }
                };
             });
             pushTimeline({ kind: "crowd", camera: d.camera, text: `Crowd detected`, ts: now });
         } else {
             setCrowdOverlays((prev) => {
                if (!prev[d.camera]) return prev;
                return { ...prev, [d.camera]: { ...prev[d.camera], personCount: 0 } };
             });
         }
      } else if (d.object_id !== undefined || d.dwell_time !== undefined) {
         if (!d.active) {
            setLoiters((prev) => {
              if (!prev[d.camera]) return prev;
              const rest = { ...prev };
              delete rest[d.camera];
              return rest;
            });
         } else {
            const dims = dimsRef.current[d.camera] ?? { width: 1280, height: 720 };
            setLoiters((prev) => ({
              ...prev,
              [d.camera]: {
                bbox: d.bbox ?? null,
                dwellSeconds: d.dwell_time ?? 0,
                detectWidth: dims.width,
                detectHeight: dims.height,
                active: true,
                receivedAt: now,
              },
            }));
            if (d.dwell_time && d.dwell_time >= 40) {
               pushTimeline({ kind: "loitering", camera: d.camera, text: `Loitering ${Math.round(d.dwell_time)}s`, ts: now });
            }
         }
      } else if (d.fire_count !== undefined || d.smoke_count !== undefined) {
          if (!d.active) {
             setFires((prev) => {
                if (!prev[d.camera]) return prev;
                const rest = { ...prev };
                delete rest[d.camera];
                return rest;
             });
          } else {
             const fc = d.fire_count || 0;
             const sc = d.smoke_count || 0;
             const detections = d.detections || [];
             const resolution = d.inference_resolution || (d.width && d.height ? [d.width, d.height] as [number, number] : null);
             if (detections.length > 0 && resolution) {
                setFires((prev) => ({
                  ...prev,
                  [d.camera]: {
                    detections,
                    inferenceResolution: resolution,
                    fireCount: fc,
                    smokeCount: sc,
                    active: true,
                    receivedAt: now,
                  },
                }));
             }
             const label = fc && sc ? "Fire + smoke detected" : fc ? "Fire detected" : "Smoke detected";
             pushTimeline({ kind: "fire", camera: d.camera, text: `${label} (fire ${fc}, smoke ${sc})`, ts: now });
          }
      } else if (d.plate_text !== undefined) {
          if (d.active) {
             const resolution = d.inference_resolution || (d.width && d.height ? [d.width, d.height] as [number, number] : null);
             if (d.bbox && resolution) {
                setLprs((prev) => ({
                  ...prev,
                  [d.camera]: {
                    plates: [{
                      bbox: d.bbox!,
                      det_confidence: d.det_confidence || 0,
                      text: d.plate_text || "",
                      raw_text: d.plate_text || "",
                      ocr_confidence: d.ocr_confidence || 0,
                      confidence: d.confidence || 0,
                    }],
                    inferenceResolution: resolution,
                    receivedAt: now,
                  },
                }));
             }
             pushTimeline({
               kind: "lpr",
               camera: d.camera,
               text: `Plate ${d.plate_text}`,
               ts: now,
               imageUrl: d.plate_crop,
               plateText: d.plate_text,
               detConf: d.det_confidence,
               ocrConf: d.ocr_confidence,
               conf: d.confidence,
             });
          }
      } else if (d.zone_id !== undefined || d.speed_ratio !== undefined) {
          if (d.active) {
              pushTimeline({
                 kind: "stopped_vehicle",
                 camera: d.camera,
                 text: `Stopped ${Math.round(d.dwell_time || 0)}s`,
                 ts: now,
                 imageUrl: d.vehicle_crop,
                 plateText: d.plate_text,
              });
          }
      } else if (d.no_helmet_bbox !== undefined) {
          if (d.active) {
              pushTimeline({
                 kind: "no_helmet",
                 camera: d.camera,
                 text: `No Helmet (${Math.round((d.confidence || 0) * 100)}%)`,
                 ts: now,
                 imageUrl: d.vehicle_crop || d.plate_crop,
                 plateText: d.plate_text,
                 conf: d.confidence,
              });
          }
      }
    } else if (msg.type === "crowd_alert" || msg.type === "loitering_alert" || msg.type === "fire_smoke_alert") {
       // fallback for old ai_worker
       if (msg.type === "crowd_alert") {
          const d = msg.data;
          setCrowdOverlays((prev) => {
            const existing = prev[d.camera];
            const isRealtime = existing && now - existing.receivedAt < 5000 && existing.clusters && existing.clusters.length > 0;
            return {
              ...prev,
              [d.camera]: {
                ...existing,
                clusterBbox: d.cluster_bbox,
                memberIndices: d.cluster_member_indices,
                clusters: isRealtime ? existing.clusters : (d.clusters || []),
                inferenceResolution: d.inference_resolution || [1920, 1080],
                personCount: d.person_count || 0,
                threshold: d.threshold || 3,
                detections: isRealtime ? existing.detections : (d.detections ?? []),
                receivedAt: now,
              },
            };
          });
       }
    }
  };

  const { connected } = useLiveChannel(handleMessage);
  const cameras = config?.cameras ?? [];

  return (
    <div className="h-full flex flex-col">
      <Header connected={connected} cameras={cameras} activeTab={tab} onTabChange={setTab} />
      <main className="flex-1 overflow-hidden">
        {tab === "live" ? (
          <div className="h-full flex">
            <div className="flex-1 overflow-y-auto">
              <CameraGrid cameras={cameras} crowd={crowdOverlays} loiter={loiters} fire={fires} lpr={lprs} stopped={stoppedVehicles} noHelmet={noHelmets} />
            </div>
            <div className="w-80 shrink-0 h-full">
              <EventsTimeline entries={timeline} />
            </div>
          </div>
        ) : (
          <div className="h-full overflow-y-auto">
            <BenchmarkPanel cameras={cameras} />
          </div>
        )}
      </main>
    </div>
  );
}
