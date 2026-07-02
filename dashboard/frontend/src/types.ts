export type Bbox = [number, number, number, number];

export interface Cluster {
  size: number;
  bbox: Bbox | null;
  member_indices: number[];
}

export interface Detection {
  bbox: Bbox;
  confidence: number;
  class?: "fire" | "smoke";
}

export interface PlateDetection {
  bbox: Bbox;
  det_confidence: number;
  text: string;
  raw_text: string;
  ocr_confidence: number;
  confidence: number;
}

export interface TrafficVehicle {
  track_id: string;
  class: string;
  bbox: Bbox;
}

export interface HelmetDetection {
  bbox: Bbox;
  class: string;
  confidence: number;
}

export interface StoppedVehicleAlert {
  camera: string;
  active: boolean;
  object_id: string;
  vehicle_class?: string;
  bbox: Bbox;
  zone_id?: string;
  dwell_time: number;
  speed_ratio: number;
  plate_text?: string;
  vehicle_crop?: string;
  inference_resolution?: [number, number];
  timestamp: string;
}

export interface NoHelmetAlert {
  camera: string;
  active: boolean;
  object_id: string;
  vehicle_bbox?: Bbox;
  rider_bbox?: Bbox;
  no_helmet_bbox: Bbox;
  confidence: number;
  plate_text?: string;
  plate_crop?: string;
  vehicle_crop?: string;
  inference_resolution?: [number, number];
  timestamp: string;
}

export interface CrowdAlert {
  camera: string;
  person_count: number;
  total_persons: number;
  threshold: number;
  event_id: string;
  model: string;
  detections: Detection[];
  cluster_bbox: Bbox | null;
  cluster_member_indices: number[];
  clusters?: Cluster[];
  inference_resolution: [number, number];
  timestamp: string;
}

export interface LoiteringAlert {
  camera: string;
  object_id: string;
  label?: string;
  dwell_seconds?: number;
  bbox?: Bbox | null;
  score?: number | null;
  active: boolean;
  is_new?: boolean;
  frame_time?: number;
  timestamp: string;
}

export interface FireSmokeAlert {
  camera: string;
  detections?: Detection[];
  fire_count?: number;
  smoke_count?: number;
  active: boolean;
  model?: string;
  inference_resolution?: [number, number] | null;
  timestamp: string;
}

export interface FrigateEvent {
  type: "new" | "update" | "end";
  after: {
    id: string;
    camera: string;
    label: string;
    current_zones?: string[];
  };
}

export interface CameraConfig {
  name: string;
  enabled: boolean;
  width: number;
  height: number;
}

export interface DashboardConfig {
  stale_seconds: number;
  alert_topic: string;
  cameras: CameraConfig[];
}

export type WsMessage =
  | { type: "crowd_alert"; data: CrowdAlert }
  | { type: "loitering_alert"; data: LoiteringAlert }
  | { type: "fire_smoke_alert"; data: FireSmokeAlert }
  | { type: "frigate_event"; data: FrigateEvent }
  | { type: "realtime_objects"; data: RealtimeObjectsMsg }
  | { type: "realtime_crowd"; data: RealtimeCrowdMsg }
  | { type: "realtime_fire_smoke"; data: RealtimeFireSmokeMsg }
  | { type: "realtime_lpr"; data: RealtimeLprMsg }
  | { type: "realtime_stopped_vehicle"; data: RealtimeStoppedVehicleMsg }
  | { type: "realtime_helmet"; data: RealtimeHelmetMsg }
  | { type: "realtime_alert"; data: RealtimeAlertMsg };

export interface BenchmarkModelResult {
  model: string;
  latency_ms: number;
  person_count: number;
  max_cluster_size: number;
  cluster_bbox: Bbox | null;
  cluster_member_indices?: number[];
  detections: Detection[];
  error: string | null;
}

export interface BenchmarkResponse {
  frame_b64: string;
  frame_width: number;
  frame_height: number;
  results: BenchmarkModelResult[];
}

export type AlertKind = "crowd" | "loitering" | "fire" | "lpr" | "stopped_vehicle" | "no_helmet";

export interface TimelineEntry {
  id: string;
  kind: AlertKind;
  camera: string;
  text: string;
  ts: number;
  imageUrl?: string;
  plateText?: string;
  detConf?: number;
  ocrConf?: number;
  conf?: number;
}

export interface CrowdOverlayState {
  detections: Detection[];
  clusters: Cluster[];
  clusterBbox: Bbox | null;
  memberIndices: number[];
  inferenceResolution: [number, number];
  personCount: number;
  threshold: number;
  receivedAt: number;
}

export interface LoiterOverlayState {
  bbox: Bbox | null;
  dwellSeconds: number;
  detectWidth: number;
  detectHeight: number;
  active: boolean;
  receivedAt: number;
}

export interface FireOverlayState {
  detections: Detection[];
  inferenceResolution: [number, number] | null;
  fireCount: number;
  smokeCount: number;
  active: boolean;
  receivedAt: number;
}

export interface LprOverlayState {
  plates: PlateDetection[];
  inferenceResolution: [number, number];
  receivedAt: number;
}

export interface StoppedVehicleOverlayState {
  vehicles: TrafficVehicle[];
  receivedAt: number;
}

export interface NoHelmetOverlayState {
  noHelmets: HelmetDetection[];
  receivedAt: number;
}

export interface RealtimeObject {
  id: string;
  track_id: string;
  class: string;
  bbox: Bbox;
  confidence: number;
  age_seconds?: number;
}
export interface RealtimeObjectsMsg {
  camera: string;
  source: string;
  frame_id: number;
  width: number;
  height: number;
  objects: RealtimeObject[];
}
export interface RealtimeCrowdMsg {
  camera: string;
  source: string;
  frame_id: number;
  width: number;
  height: number;
  person_count?: number;
  threshold?: number;
  clusters: Cluster[];
  detections?: Detection[];
}
export interface RealtimeFireSmokeMsg {
  camera: string;
  source: string;
  frame_id: number;
  width: number;
  height: number;
  detections: Detection[];
  fire_count: number;
  smoke_count: number;
}
export interface RealtimeLprMsg {
  camera: string;
  source: string;
  frame_id: number;
  width: number;
  height: number;
  plates: PlateDetection[];
  plate_count: number;
}
export interface RealtimeStoppedVehicleMsg {
  camera: string;
  source: string;
  frame_id: number;
  width: number;
  height: number;
  vehicles: TrafficVehicle[];
  zones?: any[];
}
export interface RealtimeHelmetMsg {
  camera: string;
  source: string;
  frame_id: number;
  width: number;
  height: number;
  riders: HelmetDetection[];
  helmets: HelmetDetection[];
  no_helmets: HelmetDetection[];
  associations: any[];
}
export interface RealtimeAlertMsg {
  camera: string;
  source: string;
  frame_id: number;
  active: boolean;
  timestamp: string;
  width?: number;
  height?: number;
  inference_resolution?: [number, number] | null;
  // Crowd
  person_count?: number;
  threshold?: number;
  cluster_bbox?: Bbox | null;
  cluster_member_indices?: number[];
  clusters?: Cluster[];
  // Loitering
  object_id?: string;
  dwell_time?: number;
  bbox?: Bbox | null;
  // Fire/Smoke
  fire_count?: number;
  smoke_count?: number;
  detections?: Detection[];
  plate_text?: string;
  plate_count?: number;
  stable_hits?: number;
  det_confidence?: number;
  ocr_confidence?: number;
  confidence?: number;
  plate_crop?: string;
  // Traffic
  vehicle_class?: string;
  zone_id?: string;
  speed_ratio?: number;
  vehicle_crop?: string;
  vehicle_bbox?: Bbox | null;
  rider_bbox?: Bbox | null;
  no_helmet_bbox?: Bbox | null;
}
