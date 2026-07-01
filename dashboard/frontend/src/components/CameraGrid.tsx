import type {
  CameraConfig,
  CrowdOverlayState,
  LoiterOverlayState,
  FireOverlayState,
  LprOverlayState,
} from "../types";
import CameraTile from "./CameraTile";

interface Props {
  cameras: CameraConfig[];
  crowd: Record<string, CrowdOverlayState>;
  loiter: Record<string, LoiterOverlayState>;
  fire: Record<string, FireOverlayState>;
  lpr: Record<string, LprOverlayState>;
}

export default function CameraGrid({ cameras, crowd, loiter, fire, lpr }: Props) {
  if (cameras.length === 0) {
    return <div className="p-6 text-gray-500">No enabled cameras.</div>;
  }
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3 p-3">
      {cameras.map((c) => (
        <CameraTile
          key={c.name}
          camera={c}
          crowd={crowd[c.name]}
          loiter={loiter[c.name]}
          fire={fire[c.name]}
          lpr={lpr[c.name]}
        />
      ))}
    </div>
  );
}
