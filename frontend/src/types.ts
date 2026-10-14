export type ConnectionState = 'disconnected' | 'checking' | 'connected' | 'reconnecting' | 'error'

export type BBox = [number, number, number, number]

export interface Detection {
  track_id: number | string
  bbox: BBox
  center?: [number, number]
  class_id: string
  name: string
  location?: string
  similarity_top1?: number | null
  similarity_top2?: number | null
  margin?: number | null
  validation?: string
}

export interface AnalysisResponse {
  frame_id: string
  timestamp: number
  mode: string
  simulated: boolean
  image: { width: number; height: number; orientation: string; preview_url?: string; preview_data_url?: string }
  detections: Detection[]
  arduino: {
    requested_mask: number | null
    confirmed_mask: number | null
    connected: boolean
    simulated: boolean
    ack: boolean
  }
  metrics?: AnalysisMetrics
}

export interface MetricPercentiles {
  p50_ms?: number | null
  p95_ms?: number | null
  sample_count?: number | null
}

export interface AnalysisMetrics {
  capture_fps?: number | null
  display_fps?: number | null
  inference_hz?: number | null
  inference_ms_p50?: number | null
  inference_ms_p95?: number | null
  sample_count?: number | null
  vram_mb?: number | null
  capture_to_ack_ms?: number | null
  stages?: Record<string, MetricPercentiles>
  stage_ms?: Record<string, MetricPercentiles>
  [key: string]: unknown
}

export interface SessionResponse {
  session_id: string
  ticket: string
  expires_at?: string
  lease_expires_at?: string
  role?: string
}

export interface InventoryItem {
  bit?: number
  led_index?: number
  location?: string
  name?: string
  class_id?: string
  [key: string]: unknown
}

export interface InventoryResponse {
  example?: boolean
  categories?: InventoryItem[]
}

export interface StatusResponse {
  connected?: boolean
  lease?: { active?: boolean; device_id?: string; expires_at?: string; controller?: boolean }
  arduino?: ArduinoStatus
  mode?: string
  simulated?: boolean
  maintenance_mode?: boolean
  [key: string]: unknown
}

export interface WsEvent {
  type: 'detection' | 'system' | 'arduino_ack' | string
  [key: string]: unknown
}

export interface ToolCategoryItem {
  class_id: string
  name: string
  location: string
  led_index: number
  pin: number
  descriptions: string[]
  display_name?: string | null
  reference_count?: number
  [key: string]: unknown
}

export interface InventoryConfig {
  example: boolean
  categories: ToolCategoryItem[]
}

export interface ClassifierProfile {
  threshold: number
  margin: number
}

export interface SettingsConfig {
  mode: 'mock' | 'preparation' | 'real'
  gallery_mode: 'text-only' | 'hybrid' | 'image-only'
  text_weight: number
  classifier_threshold: number
  classifier_margin: number
  classifier_profiles?: Record<string, ClassifierProfile>
  roi: [number, number, number, number] | null
  inventory_confirmed: boolean
  segmentation_min_area?: number
  segmentation_max_area?: number
  tracking_max_distance?: number
  tracking_max_missed?: number
  batch_size?: number
  frame_max_age_seconds?: number
  scene_ttl_seconds?: number
  photo_retention_seconds?: number
  prediction_max_age_seconds?: number
  prediction_interval_seconds?: number
}

export interface ConfigResponse {
  saved_version: number
  active_version: number
  is_dirty: boolean
  settings: SettingsConfig
  inventory: InventoryConfig
}

export interface ConfigTask {
  state: 'idle' | 'running' | 'completed' | 'failed' | string
  stage: string
  progress: number
  error: string | null
}

export interface ConfigStatusResponse {
  saved_version: number
  active_version: number
  is_dirty: boolean
  task: ConfigTask
}

export interface SystemReadiness {
  backend_ok: boolean
  python_version: string
  torch_available: boolean
  cuda_available: boolean
  gpu_name: string | null
  vram_free_mb: number | null
  model_present: boolean
  model_status: 'ready' | 'missing' | 'missing_dependency' | 'loading' | 'error' | string
  inventory_ready: boolean
  gallery_ready: boolean
  calibration_ready: boolean
  arduino_ready: boolean
  active_mode: 'mock' | 'preparation' | 'real' | string
  can_activate_real: boolean
  preparation_reasons: string[]
}

export interface ReferencePhoto {
  image_id: string
  filename?: string
  size_bytes?: number
  created_at?: number
  thumbnail_data_url: string
}

export interface ReferenceListResponse {
  class_id: string
  references: ReferencePhoto[]
}

export interface CalibrationMeta {
  calibrated: boolean
  status: 'ready' | 'missing' | string
  image: {
    width: number
    height: number
    orientation: string
  } | null
  roi: [number, number, number, number] | null
}

export interface ComPort {
  port: string
  description: string
}

export interface ArduinoStatus {
  connected?: boolean
  simulated?: boolean
  requested_mask?: number | null
  confirmed_mask?: number | null
  ack?: boolean
  /** Unix wall-clock seconds supplied by the backend transport status. */
  last_ack_at?: number | null
  port?: string | null
  error?: string | null
}

export interface ArduinoMaintenanceResponse {
  maintenance_mode: boolean
  arduino: ArduinoStatus
}

export interface ArduinoLedTestResponse {
  led_index: number
  mask: number
  duration_seconds: number
  arduino: ArduinoStatus
}

export type ActiveTab = 'inspection' | 'tools' | 'calibration' | 'arduino' | 'system'
