// A top-level function that is called cross-file from a method (circle.ts) —
// the deterministic call-graph target for the enclosing-range resolution.
export function circleArea(radius: number): number {
  return Math.PI * radius * radius;
}
