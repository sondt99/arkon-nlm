import { act } from "@testing-library/react";
import { vi } from "vitest";

/**
 * Advance fake timers inside an `act()` boundary.
 *
 * A bare `await vi.advanceTimersByTimeAsync(n)` fires every polling interval and debounce
 * that is due, and each `setState` those trigger lands OUTSIDE React's act boundary. React
 * then logs "An update to X inside a test was not wrapped in act(...)" for every one —
 * which flooded the CI log with hundreds of lines and buried the actual results.
 *
 * The noise was the visible symptom; the real cost is that an unwrapped update is not
 * guaranteed to have flushed its effects before the next assertion runs, so a test that
 * passes locally can fail on a slower runner. `act()` makes the flush deterministic.
 *
 * Prefer this over `vi.advanceTimersByTimeAsync` in any test that renders a component.
 * `api.test.ts` is the one legitimate exception — it advances timers to trip a fetch
 * timeout with no component mounted, so there is no React tree to reconcile.
 */
export async function tick(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}
