import { describe, expect, it } from 'vitest'

import { fmt, pct, posNeg, signed } from './format'

describe('number formatting', () => {
  it('keeps missing numbers explicit', () => {
    expect(fmt(null)).toBe('—')
    expect(pct(undefined)).toBe('—')
  })

  it('formats signed values for financial tables', () => {
    expect(fmt(1.2345, 3)).toBe('1.235')
    expect(signed(2.1)).toBe('+2.10')
    expect(signed(-2.1)).toBe('-2.10')
    expect(pct(2.1)).toBe('+2.10%')
    expect(pct(0)).toBe('0.00%')
  })

  it('returns the semantic class together with text', () => {
    expect(posNeg(1, '+1%')).toEqual({ text: '+1%', tone: 'positive' })
    expect(posNeg(-1, '-1%')).toEqual({ text: '-1%', tone: 'negative' })
    expect(posNeg(0, '0%')).toEqual({ text: '0%', tone: 'neutral' })
  })
})
