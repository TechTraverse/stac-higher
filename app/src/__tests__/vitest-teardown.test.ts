/**
 * #30: a persistent store's deferred unmount must not throw once jsdom is gone.
 *
 * nanostores runs a store's onMount cleanup 1 s after its last subscriber
 * leaves (`STORE_UNMOUNT_DELAY`, a plain setTimeout), and `.get()` on an
 * unsubscribed store mounts and unmounts it on the spot, so every file that
 * imports `catalogStore` (`ensureBuiltInCatalog()` at load) or unmounts a
 * `useStore($theme)` component ends with that timer pending. For a
 * `persistentAtom` the cleanup is `window.removeEventListener`; when vitest
 * has torn the file's jsdom down and the worker lives on for another second
 * (CI load), it threw `ReferenceError: window is not defined` and failed a
 * run whose tests all passed. This test removes `window` the way teardown
 * does and lets the timer fire.
 */
import { it, expect } from "vitest";
import { persistentAtom } from "@nanostores/persistent";

it("a persistent store's deferred unmount after the window is gone does not throw", async () => {
  const $probe = persistentAtom<string>("vitest-teardown-probe", "x");
  $probe.get(); // mounts and unmounts at once: the deferred cleanup is now armed

  const g = globalThis as { window?: unknown };
  const saved = Object.getOwnPropertyDescriptor(globalThis, "window");
  expect(saved).toBeDefined();
  delete g.window;
  expect(typeof window).toBe("undefined");
  try {
    // STORE_UNMOUNT_DELAY is 1000 ms; an error inside the timer fails the run
    // as an unhandled error, which is exactly what #30 looked like.
    await new Promise((resolve) => setTimeout(resolve, 1200));
  } finally {
    Object.defineProperty(globalThis, "window", saved!);
  }
});
