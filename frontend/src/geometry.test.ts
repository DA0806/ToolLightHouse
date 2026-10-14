import { describe, expect, it } from 'vitest'
import { imageTransform, mapBoxToViewport, orientedSize } from './geometry'

describe('normalized image geometry', () => {
  it('centers a 4:3 image in contain mode without changing boxes', () => {
    const t = imageTransform({ width: 400, height: 300 }, { width: 800, height: 800 }, 'contain')
    expect(t.scale).toBe(2)
    expect([t.offsetX, t.offsetY]).toEqual([0, 100])
    expect(mapBoxToViewport([100, 50, 80, 60], t)).toEqual([200, 200, 160, 120])
  })

  it('keeps cover crop offsets in the overlay coordinates', () => {
    const t = imageTransform({ width: 400, height: 300 }, { width: 800, height: 400 }, 'cover')
    expect(t.scale).toBeCloseTo(2)
    expect(t.offsetY).toBe(-100)
    expect(mapBoxToViewport([0, 100, 100, 50], t)).toEqual([0, 100, 200, 100])
  })

  it('rotates dimensions and boxes around the normalized image', () => {
    expect(orientedSize({ width: 400, height: 300 }, 'rotate90')).toEqual({ width: 300, height: 400 })
    const t = imageTransform({ width: 400, height: 300 }, { width: 300, height: 400 }, 'contain', 'rotate90')
    expect(mapBoxToViewport([0, 0, 100, 50], t)).toEqual([250, 0, 50, 100])
  })
})
