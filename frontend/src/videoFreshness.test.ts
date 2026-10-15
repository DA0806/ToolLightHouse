import { afterEach, describe, expect, it, vi } from 'vitest'
import { createVideoFreshnessState, waitForFreshVideoFrame } from './videoFreshness'

function fakeVideo() {
  let callback: ((now: number, metadata: { presentedFrames?: number; mediaTime?: number }) => void) | null = null
  const video = {
    readyState: 2,
    videoWidth: 640,
    videoHeight: 480,
    currentTime: 0,
    requestVideoFrameCallback: vi.fn((next) => {
      callback = next
      return 1
    }),
    cancelVideoFrameCallback: vi.fn(),
    getVideoPlaybackQuality: vi.fn(() => ({ totalVideoFrames: 4 })),
  } as unknown as HTMLVideoElement & { emit: (frames: number) => void }
  video.emit = (frames) => callback?.(0, { presentedFrames: frames, mediaTime: frames / 30 })
  return video
}

describe('video freshness gate', () => {
  afterEach(() => vi.useRealTimers())

  it('rejects a frozen decoded frame after the bounded age', async () => {
    vi.useFakeTimers()
    const video = fakeVideo()
    const state = { initialized: true, presentedFrames: 4, mediaTime: 0.13 }
    const pending = waitForFreshVideoFrame(video, state, 1000)
    video.emit(4)
    const rejection = expect(pending).rejects.toThrow(/fotograma nuevo/i)
    await vi.advanceTimersByTimeAsync(1000)
    await rejection
  })

  it('accepts the next decoded frame and advances the baseline', async () => {
    const video = fakeVideo()
    const state = createVideoFreshnessState()
    const pending = waitForFreshVideoFrame(video, state)
    video.emit(1)
    await pending
    expect(state.initialized).toBe(true)
    expect(state.presentedFrames).toBe(1)
  })
})
