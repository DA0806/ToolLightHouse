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
  arduino?: { connected?: boolean; simulated?: boolean; requested_mask?: number; confirmed_mask?: number; ack?: boolean }
  mode?: string
  simulated?: boolean
  [key: string]: unknown
}

export interface WsEvent {
  type: 'detection' | 'system' | 'arduino_ack' | string
  [key: string]: unknown
}
