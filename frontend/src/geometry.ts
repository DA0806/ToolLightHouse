export type Fit = 'contain' | 'cover'
export type Orientation = 'normal' | 'rotate90' | 'rotate180' | 'rotate270'

export interface Size { width: number; height: number }
export interface Transform {
  scale: number
  renderedWidth: number
  renderedHeight: number
  offsetX: number
  offsetY: number
  sourceWidth: number
  sourceHeight: number
  orientation: Orientation
}

export function orientedSize(source: Size, orientation: Orientation): Size {
  return orientation === 'rotate90' || orientation === 'rotate270'
    ? { width: source.height, height: source.width }
    : source
}

/** Maps a backend pixel box to the actual rendered viewport, including crop. */
export function imageTransform(source: Size, viewport: Size, fit: Fit, orientation: Orientation = 'normal'): Transform {
  const oriented = orientedSize(source, orientation)
  const scale = fit === 'cover'
    ? Math.max(viewport.width / oriented.width, viewport.height / oriented.height)
    : Math.min(viewport.width / oriented.width, viewport.height / oriented.height)
  return {
    scale,
    renderedWidth: oriented.width * scale,
    renderedHeight: oriented.height * scale,
    offsetX: (viewport.width - oriented.width * scale) / 2,
    offsetY: (viewport.height - oriented.height * scale) / 2,
    sourceWidth: source.width,
    sourceHeight: source.height,
    orientation,
  }
}

export function mapBoxToViewport(box: [number, number, number, number], transform: Transform): [number, number, number, number] {
  const [x, y, width, height] = box
  const { sourceWidth: sw, sourceHeight: sh, orientation, scale, offsetX, offsetY } = transform
  let left = x; let top = y; let right = x + width; let bottom = y + height
  if (orientation === 'rotate90') [left, top, right, bottom] = [sh - bottom, left, sh - top, right]
  if (orientation === 'rotate180') [left, top, right, bottom] = [sw - right, sh - bottom, sw - left, sh - top]
  if (orientation === 'rotate270') [left, top, right, bottom] = [top, sw - right, bottom, sw - left]
  return [offsetX + left * scale, offsetY + top * scale, (right - left) * scale, (bottom - top) * scale]
}
