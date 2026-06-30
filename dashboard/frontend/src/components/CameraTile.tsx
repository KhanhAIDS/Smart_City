import { useEffect, useRef, useState } from "react";
import type {
  CameraConfig,
  CrowdOverlayState,
  LoiterOverlayState,
  FireOverlayState,
} from "../types";
import { startWebRTC } from "../lib/liveStream";
import { snapshotUrl } from "../lib/api";

interface Props {
  camera: CameraConfig;
  crowd?: CrowdOverlayState;
  loiter?: LoiterOverlayState;
  fire?: FireOverlayState;
}

function FireOverlay({ fire }: { fire: FireOverlayState }) {
  if (!fire.inferenceResolution) return null;
  const [w, h] = fire.inferenceResolution;
  return (
    <svg
      className="absolute inset-0 w-full h-full pointer-events-none"
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="xMidYMid slice"
    >
      {fire.detections.map((d, i) => {
        const [x1, y1, x2, y2] = d.bbox;
        const color = d.class === "smoke" ? "#3b82f6" : "#ef4444";
        return (
          <g key={i}>
            <rect
              style={{ transition: "all 0.15s linear" }}
              x={x1}
              y={y1}
              width={x2 - x1}
              height={y2 - y1}
              fill="none"
              stroke={color}
              strokeWidth={4}
            />
            <text
              style={{ transition: "all 0.15s linear" }}
              x={x1}
              y={Math.max(y1 - 6, 14)}
              fill={color}
              fontSize={Math.max(h * 0.025, 14)}
              fontWeight="bold"
            >
              {d.class === "smoke" ? "SMOKE" : "FIRE"} {(d.confidence * 100).toFixed(0)}%
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function LoiterOverlay({ loiter }: { loiter: LoiterOverlayState }) {
  const [dwell, setDwell] = useState(loiter.dwellSeconds);
  useEffect(() => {
    setDwell(loiter.dwellSeconds);
    const t = setInterval(() => setDwell((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, [loiter.dwellSeconds]);

  if (!loiter.bbox) return null;
  const w = loiter.detectWidth;
  const h = loiter.detectHeight;
  const [x1, y1, x2, y2] = loiter.bbox;
  const color = "#f59e0b";
  return (
    <svg
      className="absolute inset-0 w-full h-full pointer-events-none"
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="xMidYMid slice"
    >
      <rect
              style={{ transition: "all 0.15s linear" }}
        x={x1}
        y={y1}
        width={x2 - x1}
        height={y2 - y1}
        fill="none"
        stroke={color}
        strokeWidth={3}
      />
      <text
              style={{ transition: "all 0.15s linear" }}
        x={x1}
        y={Math.max(y1 - 6, 14)}
        fill={color}
        fontSize={Math.max(h * 0.03, 14)}
        fontWeight="bold"
      >
        LOITERING {Math.round(dwell)}s
      </text>
    </svg>
  );
}

function CrowdOverlay({ crowd }: { crowd: CrowdOverlayState }) {
  if (!crowd.inferenceResolution) return null;
  const [w, h] = crowd.inferenceResolution;
  const legacyMembers = new Set(crowd.memberIndices);
  const renderClusters = crowd.clusters && crowd.clusters.length > 0;

  return (
    <svg
      className="absolute inset-0 w-full h-full pointer-events-none"
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="xMidYMid slice"
    >
      {!renderClusters && crowd.personCount >= crowd.threshold && crowd.detections && crowd.detections.map((d, i) =>
        legacyMembers.has(i) ? (
          <rect
              style={{ transition: "all 0.15s linear" }}
            key={`legacy-member-${i}`}
            x={d.bbox[0]}
            y={d.bbox[1]}
            width={d.bbox[2] - d.bbox[0]}
            height={d.bbox[3] - d.bbox[1]}
            fill="none"
            stroke="#22c55e"
            strokeWidth={2}
          />
        ) : null
      )}
      {!renderClusters && crowd.clusterBbox && crowd.personCount >= crowd.threshold && (
        <rect
              style={{ transition: "all 0.15s linear" }}
          x={crowd.clusterBbox[0]}
          y={crowd.clusterBbox[1]}
          width={crowd.clusterBbox[2] - crowd.clusterBbox[0]}
          height={crowd.clusterBbox[3] - crowd.clusterBbox[1]}
          fill="none"
          stroke="#ef4444"
          strokeWidth={3}
          strokeDasharray="10 6"
        />
      )}

      {renderClusters && crowd.clusters.map((c, idx) => {
        if (c.size < crowd.threshold || crowd.personCount < crowd.threshold) return null;
        const members = new Set(c.member_indices);
        return (
          <g key={`cluster-${idx}`}>
            {crowd.detections.map((d, i) =>
              members.has(i) ? (
                <rect
              style={{ transition: "all 0.15s linear" }}
                  key={`member-${idx}-${i}`}
                  x={d.bbox[0]}
                  y={d.bbox[1]}
                  width={d.bbox[2] - d.bbox[0]}
                  height={d.bbox[3] - d.bbox[1]}
                  fill="none"
                  stroke="#22c55e"
                  strokeWidth={2}
                />
              ) : null
            )}
            {c.bbox && (
              <rect
              style={{ transition: "all 0.15s linear" }}
                x={c.bbox[0]}
                y={c.bbox[1]}
                width={c.bbox[2] - c.bbox[0]}
                height={c.bbox[3] - c.bbox[1]}
                fill="none"
                stroke="#ef4444"
                strokeWidth={3}
                strokeDasharray="10 6"
              />
            )}
          </g>
        );
      })}
    </svg>
  );
}

async function captureFrame(camera: string) {
  try {
    const r = await fetch(`/api/${camera}/latest.jpg?t=${Date.now()}`);
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${camera}-${Date.now()}.jpg`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } catch (e) {
    console.error("capture failed", e);
  }
}

export default function CameraTile({ camera, crowd, loiter, fire }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [webrtcFailed, setWebrtcFailed] = useState(false);
  const [forceSnapshot, setForceSnapshot] = useState(false);
  const [imgSrc, setImgSrc] = useState("");

  const useSnapshot = forceSnapshot || webrtcFailed;

  useEffect(() => {
    let active = true;
    let stopStream: (() => void) | undefined;
    let retryTimeout: number | undefined;
    
    const connect = () => {
      if (!active || !videoRef.current) return;
      if (stopStream) stopStream();
      
      stopStream = startWebRTC(videoRef.current, camera.name, 
        // onError
        () => {
          if (active) {
            console.warn(`WebRTC failed for ${camera.name}, retrying in 3s...`);
            setWebrtcFailed(true);
            retryTimeout = window.setTimeout(connect, 3000);
          }
        },
        // onSuccess
        () => {
          if (active) {
            setWebrtcFailed(false);
          }
        }
      );
    };
    connect();

    return () => {
      active = false;
      if (stopStream) stopStream();
      if (retryTimeout) clearTimeout(retryTimeout);
    };
  }, [camera.name]);

  useEffect(() => {
    if (!useSnapshot) return;
    const tick = () => setImgSrc(snapshotUrl(camera.name, 360));
    tick();
    const t = setInterval(tick, 800);
    return () => clearInterval(t);
  }, [useSnapshot, camera.name]);

  const crowdOver = !!crowd && crowd.personCount >= crowd.threshold;
  const fireActive = !!fire && fire.active;
  const loiterActive = !!loiter && loiter.active;

  let border = "border-gray-800";
  if (fireActive) border = "border-red-600";
  else if (crowdOver) border = "border-red-500";
  else if (loiterActive) border = "border-amber-500";

  return (
    <div className={`relative rounded-lg overflow-hidden border-2 ${border} bg-black aspect-video`}>
      <video ref={videoRef} autoPlay muted playsInline className={`w-full h-full object-cover ${useSnapshot && !forceSnapshot ? 'opacity-0 absolute inset-0' : 'block'}`} />
      
      {useSnapshot && (
        <img src={imgSrc} alt={camera.name} className="w-full h-full object-cover" />
      )}

      {webrtcFailed && !forceSnapshot && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/50 text-white text-sm z-10 pointer-events-none">
          WebRTC Reconnecting... (Snapshot fallback active)
        </div>
      )}

      {fire && fire.active && <FireOverlay fire={fire} />}
      {loiter && loiter.active && <LoiterOverlay loiter={loiter} />}
      {crowd && <CrowdOverlay crowd={crowd} />}

      <div className="absolute top-0 inset-x-0 flex items-center justify-between px-2 py-1 bg-gradient-to-b from-black/70 to-transparent z-20">
        <span className="text-xs font-medium text-gray-100">{camera.name}</span>
        <div className="flex items-center gap-1">
          {webrtcFailed && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-600/80 text-white cursor-pointer pointer-events-auto" onClick={() => setWebrtcFailed(false)}>
              ERROR
            </span>
          )}
          <button
            onClick={() => setForceSnapshot((v) => !v)}
            title="Toggle snapshot mode"
            className="text-[10px] px-1.5 py-0.5 rounded bg-gray-700/80 hover:bg-gray-600 text-white pointer-events-auto"
          >
            {forceSnapshot ? "Live" : "Snap"}
          </button>
          <button
            onClick={() => captureFrame(camera.name)}
            title="Capture clean frame (download)"
            className="text-[10px] px-1.5 py-0.5 rounded bg-gray-700/80 hover:bg-gray-600 text-white pointer-events-auto"
          >
            📷
          </button>
        </div>
      </div>

      <div className="absolute bottom-0 inset-x-0 flex items-center gap-2 px-2 py-1 bg-gradient-to-t from-black/70 to-transparent z-20">
        {crowdOver && (
          <div className="bg-red-500/90 text-white text-xs font-bold px-2 py-1 rounded shadow-lg backdrop-blur-sm">
            CROWD {crowd!.personCount}
            {crowd!.clusters && crowd!.clusters.length > 0 && (
              <span className="ml-1 opacity-90 font-medium">
                ({crowd!.clusters.filter(c => c.size >= crowd!.threshold).length} groups)
              </span>
            )}
          </div>
        )}
        {fireActive && (
          <span className="text-xs px-2 py-0.5 rounded bg-red-600 text-white font-semibold animate-pulse">
            🔥 FIRE {fire!.fireCount}
            {fire!.smokeCount > 0 ? ` · SMOKE ${fire!.smokeCount}` : ""}
          </span>
        )}
        {loiterActive && (
          <span className="text-xs px-2 py-0.5 rounded bg-amber-500 text-black font-semibold">
            LOITERING
          </span>
        )}
      </div>
    </div>
  );
}
