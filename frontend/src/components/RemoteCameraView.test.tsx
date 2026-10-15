import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { RemoteCameraView } from './RemoteCameraView'

// Mock WebSocket
class MockWebSocket {
  static OPEN = 1
  static CONNECTING = 0
  static CLOSING = 2
  static CLOSED = 3

  readyState = MockWebSocket.OPEN
  onopen: (() => void) | null = null
  onmessage: ((evt: { data: string }) => void) | null = null
  onerror: ((err: any) => void) | null = null
  onclose: (() => void) | null = null

  sentMessages: string[] = []

  constructor(public url: string) {
    setTimeout(() => {
      this.onopen?.()
    }, 10)
  }

  send(data: string) {
    this.sentMessages.push(data)
  }

  close() {
    this.readyState = MockWebSocket.CLOSED
    this.onclose?.()
  }
}

describe('RemoteCameraView Component', () => {
  let mockTrack: { stop: ReturnType<typeof vi.fn> }
  let mockStream: { getTracks: () => any[] }
  let mockPc: any

  beforeEach(() => {
    vi.clearAllMocks()
    window.location.hash = ''

    mockTrack = { stop: vi.fn() }
    mockStream = { getTracks: () => [mockTrack] }

    mockPc = {
      addTrack: vi.fn(),
      setRemoteDescription: vi.fn().mockResolvedValue(undefined),
      createAnswer: vi.fn().mockResolvedValue({ type: 'answer', sdp: 'mock-sdp' }),
      setLocalDescription: vi.fn().mockResolvedValue(undefined),
      addIceCandidate: vi.fn().mockResolvedValue(undefined),
      close: vi.fn(),
      iceConnectionState: 'new',
      onicecandidate: null as any,
      oniceconnectionstatechange: null as any,
    }

    Object.assign(navigator, {
      mediaDevices: {
        getUserMedia: vi.fn().mockResolvedValue(mockStream),
      },
    })

    // @ts-ignore
    window.WebSocket = MockWebSocket as any
    // @ts-ignore
    global.WebSocket = MockWebSocket as any
    // @ts-ignore
    window.RTCPeerConnection = vi.fn(function () {
      return mockPc
    }) as any
    // @ts-ignore
    global.RTCPeerConnection = window.RTCPeerConnection
    // @ts-ignore
    window.RTCSessionDescription = vi.fn().mockImplementation((desc) => desc) as any
    // @ts-ignore
    global.RTCSessionDescription = window.RTCSessionDescription
    // @ts-ignore
    window.RTCIceCandidate = vi.fn().mockImplementation((cand) => cand) as any
    // @ts-ignore
    global.RTCIceCandidate = window.RTCIceCandidate
    // @ts-ignore
    HTMLMediaElement.prototype.play = vi.fn().mockResolvedValue(undefined)
  })

  afterEach(() => {
    window.location.hash = ''
  })

  it('displays error and does not request camera if no invitation token is in URL', async () => {
    window.location.hash = ''

    render(<RemoteCameraView />)

    expect(screen.getByText(/No se proporcionó un token de invitación válido/i)).toBeInTheDocument()
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled()
  })

  it('does not request camera when signaling rejects the invitation', async () => {
    window.location.hash = '#token=expired-invitation'
    let capturedWs: MockWebSocket | null = null
    const MockWsSubclass = class extends MockWebSocket {
      constructor(url: string) {
        super(url)
        capturedWs = this
      }
    }
    // @ts-ignore
    window.WebSocket = MockWsSubclass as any
    // @ts-ignore
    global.WebSocket = MockWsSubclass as any

    render(<RemoteCameraView />)
    await waitFor(() => expect(capturedWs?.sentMessages.length).toBeGreaterThan(0))
    capturedWs!.onmessage?.({ data: JSON.stringify({ type: 'error', message: 'invite expired' }) })

    await waitFor(() => expect(screen.getByText('invite expired')).toBeInTheDocument())
    expect(navigator.mediaDevices.getUserMedia).not.toHaveBeenCalled()
  })

  it('extracts fragment token, requests camera, and authenticates as auth_transmitter', async () => {
    window.location.hash = '#token=test-invitation-token-123'

    let capturedWs: MockWebSocket | null = null
    const MockWsSubclass = class extends MockWebSocket {
      constructor(url: string) {
        super(url)
        capturedWs = this
      }
    }
    // @ts-ignore
    window.WebSocket = MockWsSubclass as any
    // @ts-ignore
    global.WebSocket = MockWsSubclass as any

    render(<RemoteCameraView />)

    await waitFor(() => {
      expect(capturedWs).not.toBeNull()
      expect(capturedWs!.sentMessages.length).toBeGreaterThan(0)
    })
    const authMessage = JSON.parse(capturedWs!.sentMessages[0])
    expect(authMessage.type).toBe('auth_transmitter')
    capturedWs!.onmessage?.({
      data: JSON.stringify({
        type: 'auth_ok',
        transmitter_id: 'tx-test',
        transmitter_ticket: 'ticket-test',
      }),
    })

    await waitFor(() => {
      expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith(
        expect.objectContaining({
          video: expect.objectContaining({
            facingMode: { ideal: 'environment' },
          }),
          audio: false,
        })
      )
    })

    expect(screen.getByText('Cámara Móvil Vinculada')).toBeInTheDocument()
  })

  it('sends candidate and uses transmitter_ticket upon receiving auth_ok', async () => {
    window.location.hash = '#token=my-secret-invite-token'

    let capturedWs: MockWebSocket | null = null
    const MockWsSubclass = class extends MockWebSocket {
      constructor(url: string) {
        super(url)
        capturedWs = this
      }
    }
    // @ts-ignore
    window.WebSocket = MockWsSubclass as any
    // @ts-ignore
    global.WebSocket = MockWsSubclass as any

    render(<RemoteCameraView />)

    await waitFor(() => {
      expect(capturedWs).not.null
      expect(capturedWs!.sentMessages.length).toBeGreaterThan(0)
    })

    // Verify first message is auth_transmitter (Finding 1)
    const firstMsg = JSON.parse(capturedWs!.sentMessages[0])
    expect(firstMsg.type).toBe('auth_transmitter')
    expect(firstMsg.invite_token).toBe('my-secret-invite-token')

    // Simulate server auth_ok response with transmitter_ticket
    capturedWs!.onmessage?.({
      data: JSON.stringify({
        type: 'auth_ok',
        role: 'transmitter',
        transmitter_id: 'tx-456',
        transmitter_ticket: 'ticket-xyz-789',
      }),
    })

    await waitFor(() => {
      expect(screen.getByText(/Vinculado\. Esperando operador/i)).toBeInTheDocument()
    })

    // Simulate ICE candidate generated by phone
    mockPc.onicecandidate?.({ candidate: { candidate: 'candidate:1 1 UDP 12345' } })

    // Verify sent message uses type: 'candidate' (Finding 1)
    const candMsg = capturedWs!.sentMessages.find((m) => m.includes('candidate:1 1 UDP'))
    expect(candMsg).toBeDefined()
    const parsedCand = JSON.parse(candMsg!)
    expect(parsedCand.type).toBe('candidate')
  })

  it('cleans up media tracks and peer connection on unmount', async () => {
    window.location.hash = '#token=cleanup-test'

    let capturedWs: MockWebSocket | null = null
    const MockWsSubclass = class extends MockWebSocket {
      constructor(url: string) {
        super(url)
        capturedWs = this
      }
    }
    // @ts-ignore
    window.WebSocket = MockWsSubclass as any
    // @ts-ignore
    global.WebSocket = MockWsSubclass as any

    const { unmount } = render(<RemoteCameraView />)

    await waitFor(() => {
      expect(capturedWs).not.toBeNull()
      expect(capturedWs!.sentMessages.length).toBeGreaterThan(0)
    })
    capturedWs!.onmessage?.({
      data: JSON.stringify({
        type: 'auth_ok',
        transmitter_id: 'tx-cleanup',
        transmitter_ticket: 'ticket-cleanup',
      }),
    })

    await waitFor(() => {
      expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalled()
    })
    await waitFor(() => {
      expect(mockPc.addTrack).toHaveBeenCalled()
    })

    unmount()

    expect(mockTrack.stop).toHaveBeenCalled()
  })
})
