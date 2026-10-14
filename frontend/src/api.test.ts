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
})
