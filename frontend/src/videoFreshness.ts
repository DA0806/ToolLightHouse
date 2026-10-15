export const VIDEO_FRAME_MAX_AGE_MS = 1000

export interface VideoFreshnessState {
  initialized: boolean
  presentedFrames: number | null
  mediaTime: number | null
}

export function createVideoFreshnessState(): VideoFreshnessState {
  return { initialized: false, presentedFrames: null, mediaTime: null }
}

type VideoWithFrameCallback = HTMLVideoElement & {
  requestVideoFrameCallback?: (callback: (now: number, metadata: { presentedFrames?: number; mediaTime?: number }) => void) => number
  cancelVideoFrameCallback?: (handle: number) => void
}

function progress(video: HTMLVideoElement, metadata?: { presentedFrames?: number; mediaTime?: number }) {
  const candidate = video as VideoWithFrameCallback
  const quality = typeof video.getVideoPlaybackQuality === 'function' ? video.getVideoPlaybackQuality() : undefined
  const presentedFrames = Number.isFinite(metadata?.presentedFrames)
    ? Number(metadata?.presentedFrames)
    : Number.isFinite(quality?.totalVideoFrames)
      ? Number(quality?.totalVideoFrames)
      : null
  const mediaTime = Number.isFinite(metadata?.mediaTime)
    ? Number(metadata?.mediaTime)
    : Number.isFinite(video.currentTime)
      ? Number(video.currentTime)
      : null
  return { presentedFrames, mediaTime }
}

function hasAdvanced(previous: VideoFreshnessState, next: { presentedFrames: number | null; mediaTime: number | null }) {
  if (!previous.initialized) return true
  if (previous.presentedFrames !== null && next.presentedFrames !== null) return next.presentedFrames > previous.presentedFrames
  if (previous.mediaTime !== null && next.mediaTime !== null) return next.mediaTime > previous.mediaTime + 0.001
  return false
}

export function waitForFreshVideoFrame(
  video: HTMLVideoElement,
  state: VideoFreshnessState,
  maxAgeMs = VIDEO_FRAME_MAX_AGE_MS,
): Promise<void> {
  if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
    return Promise.reject(new Error('El vídeo no está listo para capturar. Comprueba el permiso y la conexión de la cámara.'))
  }
  const candidate = video as VideoWithFrameCallback
  return new Promise((resolve, reject) => {
    let settled = false
    let callbackHandle: number | null = null
    let pollHandle: number | null = null
    const timer = window.setTimeout(() => finish(new Error('El vídeo no ha recibido un fotograma nuevo. Comprueba la cámara o reconecta el teléfono.')), maxAgeMs)
    const finish = (error?: Error) => {
      if (settled) return
      settled = true
      window.clearTimeout(timer)
      if (pollHandle !== null) window.clearInterval(pollHandle)
      if (callbackHandle !== null && candidate.cancelVideoFrameCallback) candidate.cancelVideoFrameCallback(callbackHandle)
      if (error) reject(error)
      else resolve()
    }
    const accept = (next: { presentedFrames: number | null; mediaTime: number | null }) => {
      if (hasAdvanced(state, next)) {
        state.initialized = true
        state.presentedFrames = next.presentedFrames
        state.mediaTime = next.mediaTime
        finish()
      }
    }
    if (candidate.requestVideoFrameCallback) {
      const onFrame = (_now: number, metadata: { presentedFrames?: number; mediaTime?: number }) => {
        if (settled) return
        accept(progress(video, metadata))
        if (!settled) callbackHandle = candidate.requestVideoFrameCallback!(onFrame)
      }
      callbackHandle = candidate.requestVideoFrameCallback(onFrame)
    } else {
      pollHandle = window.setInterval(() => accept(progress(video)), 50)
      accept(progress(video))
    }
  })
}
