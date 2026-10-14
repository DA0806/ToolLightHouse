import { beforeEach, describe, expect, it, vi } from 'vitest'
import { analyzePhoto, calibrateBackground, createSession, createWsTicket, defaultApiUrl, getInventory, openEvents } from './api'

describe('ToolLighthouse API client', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('uses the local backend port while developing the Vite dashboard', () => {
    expect(defaultApiUrl()).toBe(window.location.origin)
  })

  it('sends pairing/auth headers and multipart photos', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ session_id: 's1', ticket: 't1' }), { status: 200 }))
    const session = await createSession('http://laptop:8000/', 'pair', 'phone-1', 'controller')
    expect(session.session_id).toBe('s1')
    expect(fetchMock.mock.calls[0][0]).toBe('http://laptop:8000/v1/sessions')
    expect((fetchMock.mock.calls[0][1]?.headers as Record<string, string>)['X-Pairing-Key']).toBe('pair')

    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ frame_id: 'f1', image: { width: 2, height: 2, orientation: 'normalized' }, detections: [], arduino: { requested_mask: 0, confirmed_mask: 0, connected: true, simulated: true, ack: true } }), { status: 200 }))
    await analyzePhoto('http://laptop:8000', { sessionId: 's1', ticket: 't1', role: 'controller' }, new Blob(['x'], { type: 'image/jpeg' }))
    const request = fetchMock.mock.calls[1][1]!
    expect(request.method).toBe('POST')
    expect(request.body).toBeInstanceOf(FormData)
    expect((request.headers as Record<string, string>)['X-Session-Id']).toBe('s1')
  })

  it('exposes inventory and calibration through the documented endpoints', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ categories: [{ bit: 0, name: 'Llave' }] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ stored: true }), { status: 200 }))
    await expect(getInventory('http://laptop:8000')).resolves.toEqual({ categories: [{ bit: 0, name: 'Llave' }] })
    await calibrateBackground('http://laptop:8000', { sessionId: 's1', ticket: 't1', role: 'controller' }, new Blob(['x'], { type: 'image/jpeg' }))
    expect(fetchMock.mock.calls[1][0]).toBe('http://laptop:8000/v1/calibration/background')
    expect((fetchMock.mock.calls[1][1]?.headers as Record<string, string>)['X-Session-Ticket']).toBe('t1')
  })

  it('passes both browser-safe websocket credentials as query parameters', () => {
    const socket = { close: vi.fn() }
    const WebSocketMock = vi.fn(class { close = socket.close })
    vi.stubGlobal('WebSocket', WebSocketMock)
    openEvents('https://laptop:8000', { sessionId: 's 1', ticket: 't/1', wsTicket: 'once/1', role: 'controller' }, vi.fn(), vi.fn())
    expect(WebSocketMock).toHaveBeenCalledWith('wss://laptop:8000/v1/ws?ticket=once%2F1&session_id=s+1')
  })

  it('obtains a one-use websocket ticket without exposing the session ticket', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ ticket: 'once' }), { status: 200 }))
    await expect(createWsTicket('http://laptop:8000', { sessionId: 's1', ticket: 'permanent', role: 'controller' })).resolves.toEqual({ ticket: 'once' })
    expect(fetchMock.mock.calls[0][0]).toBe('http://laptop:8000/v1/sessions/s1/ws-ticket')
    expect((fetchMock.mock.calls[0][1]?.headers as Record<string, string>)['X-Session-Ticket']).toBe('permanent')
  })

  it('creates admin session using X-Admin-Key header', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ session_id: 'adm-1', ticket: 't-adm', role: 'admin' }), { status: 200 }))
    const res = await (await import('./api')).createSession('http://laptop:8000', 'admin-secret-key', 'admin-pc', 'admin')
    expect(res.session_id).toBe('adm-1')
    expect(res.role).toBe('admin')
    const headers = fetchMock.mock.calls[0][1]?.headers as Record<string, string>
    expect(headers['X-Admin-Key']).toBe('admin-secret-key')
    expect(headers['X-Pairing-Key']).toBeUndefined()
  })

  it('manages configuration lifecycle: get, update, apply, rollback, export, import', async () => {
    const { getConfig, updateConfig, applyConfig, rollbackConfig, exportConfig, importConfig } = await import('./api')
    const adminCreds = { sessionId: 'adm-1', ticket: 't-adm', role: 'admin' as const }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ saved_version: 1, active_version: 1, is_dirty: false }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ saved_version: 2, active_version: 1, is_dirty: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ saved_version: 2, active_version: 2, is_dirty: false }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ saved_version: 1, active_version: 1, is_dirty: false }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ schema_version: 1, settings: {} }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ saved_version: 3, active_version: 1, is_dirty: true }), { status: 200 }))

    const cfg = await getConfig('http://laptop:8000', adminCreds)
    expect(cfg.saved_version).toBe(1)

    const updated = await updateConfig('http://laptop:8000', adminCreds, { expected_version: 1, settings: { mode: 'mock' }, inventory: { example: false, categories: [] } })
    expect(updated.saved_version).toBe(2)
    expect(fetchMock.mock.calls[1][1]?.method).toBe('PUT')

    const applied = await applyConfig('http://laptop:8000', adminCreds)
    expect(applied.active_version).toBe(2)
    expect(fetchMock.mock.calls[2][1]?.method).toBe('POST')

    const rolledBack = await rollbackConfig('http://laptop:8000', adminCreds)
    expect(rolledBack.saved_version).toBe(1)
    expect(fetchMock.mock.calls[3][1]?.method).toBe('POST')

    const exported = await exportConfig('http://laptop:8000', adminCreds)
    expect(exported.schema_version).toBe(1)

    const imported = await importConfig('http://laptop:8000', adminCreds, { schema_version: 1 })
    expect(imported.saved_version).toBe(3)
  })

  it('handles system readiness, mode switching, and model download instructions', async () => {
    const { getReadiness, setSystemMode, downloadModelInstructions } = await import('./api')
    const adminCreds = { sessionId: 'adm-1', ticket: 't-adm', role: 'admin' as const }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ backend_ok: true, can_activate_real: false, active_mode: 'mock' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ mode: 'preparation', readiness: { can_activate_real: false } }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: 'instructions', detail: 'huggingface-cli download ...' }), { status: 200 }))

    const r = await getReadiness('http://laptop:8000', adminCreds)
    expect(r.backend_ok).toBe(true)

    const m = await setSystemMode('http://laptop:8000', adminCreds, 'preparation')
    expect(m.mode).toBe('preparation')

    const inst = await downloadModelInstructions('http://laptop:8000', adminCreds)
    expect(inst.status).toBe('instructions')
    expect(inst.detail).toContain('huggingface-cli')
  })

  it('manages visual reference images: list, upload, delete', async () => {
    const { listReferences, uploadReference, deleteReference } = await import('./api')
    const adminCreds = { sessionId: 'adm-1', ticket: 't-adm', role: 'admin' as const }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ class_id: 'tool_1', references: [{ image_id: 'img1', thumbnail_data_url: 'data:...' }] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ image_id: 'img2', class_id: 'tool_1', thumbnail_data_url: 'data:new' }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ deleted: true, image_id: 'img1', class_id: 'tool_1' }), { status: 200 }))

    const list = await listReferences('http://laptop:8000', adminCreds, 'tool_1')
    expect(list.references).toHaveLength(1)

    const uploaded = await uploadReference('http://laptop:8000', adminCreds, 'tool_1', new Blob(['jpg'], { type: 'image/jpeg' }))
    expect(uploaded.image_id).toBe('img2')
    expect(fetchMock.mock.calls[1][1]?.method).toBe('POST')
    expect(fetchMock.mock.calls[1][1]?.body).toBeInstanceOf(FormData)

    const deleted = await deleteReference('http://laptop:8000', adminCreds, 'tool_1', 'img1')
    expect(deleted.deleted).toBe(true)
    expect(fetchMock.mock.calls[2][1]?.method).toBe('DELETE')
  })

  it('manages tray calibration and ROI coordinates', async () => {
    const { getCalibration, getCalibrationBackgroundBlob, updateRoi } = await import('./api')
    const adminCreds = { sessionId: 'adm-1', ticket: 't-adm', role: 'admin' as const }
    const blobResponse = new Blob(['fake-jpg'], { type: 'image/jpeg' })
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ calibrated: true, roi: [10, 10, 200, 200] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(blobResponse, { status: 200, headers: { 'Content-Type': 'image/jpeg' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ calibrated: true, roi: [20, 30, 400, 300] }), { status: 200 }))

    const meta = await getCalibration('http://laptop:8000', adminCreds)
    expect(meta.calibrated).toBe(true)

    const blob = await getCalibrationBackgroundBlob('http://laptop:8000', adminCreds)
    expect(blob).toBeInstanceOf(Blob)
    expect(fetchMock.mock.calls[1][0]).toBe('http://laptop:8000/v1/calibration/background.jpg')
    expect((fetchMock.mock.calls[1][1]?.headers as Record<string, string>)['X-Session-Ticket']).toBe('t-adm')

    const roiRes = await updateRoi('http://laptop:8000', adminCreds, [20, 30, 400, 300])
    expect(roiRes.roi).toEqual([20, 30, 400, 300])
    expect(fetchMock.mock.calls[2][1]?.method).toBe('PUT')
  })

  it('controls Arduino hardware maintenance and single LED test', async () => {
    const { getArduinoPorts, connectArduino, disconnectArduino, enterMaintenance, leaveMaintenance, testLed, allOff } = await import('./api')
    const adminCreds = { sessionId: 'adm-1', ticket: 't-adm', role: 'admin' as const }
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({ ports: [{ port: 'COM3', description: 'Arduino Nano' }] }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ connected: true, arduino: { connected: true, simulated: false } }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ maintenance_mode: true, arduino: { connected: true } }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ led_index: 3, mask: 8, duration_seconds: 1.5, arduino: { requested_mask: 8, confirmed_mask: 8, ack: true } }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ arduino: { requested_mask: 0, confirmed_mask: 0, ack: true } }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ maintenance_mode: false, arduino: { connected: true } }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ arduino: { connected: false } }), { status: 200 }))

    const ports = await getArduinoPorts('http://laptop:8000', adminCreds)
    expect(ports.ports).toHaveLength(1)
    expect(ports.ports[0].port).toBe('COM3')

    const conn = await connectArduino('http://laptop:8000', adminCreds, { port: 'COM3' })
    expect(conn.connected).toBe(true)

    const maint = await enterMaintenance('http://laptop:8000', adminCreds)
    expect(maint.maintenance_mode).toBe(true)

    const testRes = await testLed('http://laptop:8000', adminCreds, 3, 1.5)
    expect(testRes.led_index).toBe(3)
    expect(testRes.mask).toBe(8)
    expect(fetchMock.mock.calls[3][0]).toContain('/v1/arduino/maintenance/test-led/3?duration=1.5')

    const off = await allOff('http://laptop:8000', adminCreds)
    expect(off.arduino.requested_mask).toBe(0)

    const leave = await leaveMaintenance('http://laptop:8000', adminCreds)
    expect(leave.maintenance_mode).toBe(false)

    const disc = await disconnectArduino('http://laptop:8000', adminCreds)
    expect(disc.arduino.connected).toBe(false)
  })
})
