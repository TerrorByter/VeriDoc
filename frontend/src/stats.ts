/** How far a rate measured on few cases can be trusted: the same Wilson interval as gldb/stats.py, for the counts
 * the controls page reads from the saved reports. */

const Z_95 = 1.959964;

/** The 95% Wilson score interval for *successes* of *trials*; [0, 1] when there are none. */
export function wilson(successes: number, trials: number): [number, number] {
  if (trials <= 0) return [0, 1];
  const p = successes / trials;
  const z2 = Z_95 * Z_95;
  const centre = p + z2 / (2 * trials);
  const spread = Z_95 * Math.sqrt((p * (1 - p)) / trials + z2 / (4 * trials * trials));
  const scale = 1 + z2 / trials;
  return [Math.max(0, (centre - spread) / scale), Math.min(1, (centre + spread) / scale)];
}

/** "78–100%": where the true rate could be, given the count */
export function interval(successes: number, trials: number): string {
  if (trials <= 0) return "no cases";
  const [low, high] = wilson(successes, trials);
  return `${Math.round(low * 100)}–${Math.round(high * 100)}%`;
}
