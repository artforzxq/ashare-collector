import { describe, expect, it } from 'vitest'

import { navigationSections } from './navigation'

describe('navigation', () => {
  it('keeps every destination unique', () => {
    const destinations = navigationSections.flatMap((section) =>
      section.items.map((item) => item.to),
    )

    expect(new Set(destinations).size).toBe(destinations.length)
  })

  it('puts the read-only trade journal in the decision group', () => {
    const decisionGroup = navigationSections.find((section) => section.label === '决策')
    const journal = decisionGroup?.items.find((item) => item.label === '交易账本')

    expect(journal?.to).toBe('/journal')
  })
})
