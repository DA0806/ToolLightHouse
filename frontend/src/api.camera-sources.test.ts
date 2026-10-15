import { afterEach, describe, expect, it, vi } from 'vitest'
import { decodeCameraSourcesResponse, getCameraSources } from './api'

const canonicalResponse = {
  sources: [
    {
      source_id: 'remote_phone',
      display_name: 'Teléfono Remoto (WebRTC)',
      kind: 'webrtc_remote',
      connection_type: 'webrtc',
      status: 'active',
      current_resolution: { width: 1920, height: 1080 },
      current_fps: 30,
      is_active: true,
      calibration_profile_available: false,
      last_error: null,
    },
    {
      source_id: 'usb_webcam',
      display_name: 'Webcam USB Externa',
      kind: 'opencv_usb',
      connection_type: 'usb',
      status: 'standby',
      current_resolution: { width: 1280, height: 720 },
      current_fps: 15,
      is_active: false,
      calibration_profile_available: true,
      last_error: null,
    },
  ],
  active_source_id: 'remote_phone',
  source_version: 4,
}

describe('camera source API boundary', () => {
  afterEach(() => vi.restoreAllMocks())

  it('decodes the backend descriptor shape into the safe UI shape', () => {
    const decoded = decodeCameraSourcesResponse(canonicalResponse)
    expect(decoded.sources[0]).toMatchObject({
      source_id: 'remote_phone',
      name: 'Teléfono Remoto (WebRTC)',
      kind: 'webrtc',
      resolution: { width: 1920, height: 1080 },
      fps: 30,
      calibration_available: false,
    })
    expect(decoded.sources[1].requires_operator_action).toBe(false)
  })

  it('fails closed when a source omits its canonical resolution', () => {
    expect(() => decodeCameraSourcesResponse({
      ...canonicalResponse,
      sources: [{ ...canonicalResponse.sources[0], current_resolution: undefined }],
    })).toThrow(/resolución|contrato/i)
  })

  it('fails closed for a source id outside the backend canonical set', () => {
    expect(() => decodeCameraSourcesResponse({
      ...canonicalResponse,
      sources: [{ ...canonicalResponse.sources[0], source_id: 'invented_camera' }],
    })).toThrow(/Fuente de c.mara no soportada/i)
  })

  it('normalizes a real fetch response before callers see it', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(canonicalResponse), { status: 200 })))
    const result = await getCameraSources('http://localhost:8000', {
      sessionId: 'session', ticket: 'ticket', role: 'controller',
    })
    expect(result.sources[0].name).toBe('Teléfono Remoto (WebRTC)')
    expect(fetch).toHaveBeenCalledWith(
      'http://localhost:8000/v1/camera/sources',
      expect.objectContaining({ headers: { 'X-Session-Id': 'session', 'X-Session-Ticket': 'ticket' }, signal: expect.any(AbortSignal) }),
    )
  })
})
