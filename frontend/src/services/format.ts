export type FinancialTone = 'positive' | 'negative' | 'neutral'

export function fmt(value: number | string | null | undefined, digits = 2): string {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  return Number.isFinite(number) ? rounded(number, digits).toFixed(digits) : '—'
}

export function signed(value: number | string | null | undefined, digits = 2): string {
  if (value === null || value === undefined || value === '') return '—'
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  return `${number > 0 ? '+' : ''}${rounded(number, digits).toFixed(digits)}`
}

export function pct(value: number | string | null | undefined, digits = 2): string {
  const text = signed(value, digits)
  return text === '—' ? text : `${text}%`
}

export function posNeg(value: number | null | undefined, text: string) {
  const tone: FinancialTone = value === null || value === undefined || value === 0
    ? 'neutral'
    : value > 0
      ? 'positive'
      : 'negative'
  return { text, tone }
}

function rounded(value: number, digits: number): number {
  const factor = 10 ** digits
  return Math.round((value + Number.EPSILON * Math.sign(value || 1)) * factor) / factor
}
