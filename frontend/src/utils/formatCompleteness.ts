// One rounding for every completeness figure. Rounds to the nearest whole
// percent, but never shows 100 while the exact value is below it (99.6 -> 99).
export function formatCompleteness(percentage: number): string {
  const rounded = Math.round(percentage);
  return `${percentage < 100 ? Math.min(rounded, 99) : rounded}%`;
}
