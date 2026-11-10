import "@testing-library/jest-dom/vitest";
import { setPersistentEngine, windowPersistentEvents } from "@nanostores/persistent";

// #30: a persistentAtom's listeners must not outlive jsdom. nanostores runs a
// store's onMount cleanup in a plain setTimeout 1 s after its last subscriber
// leaves (STORE_UNMOUNT_DELAY), and `.get()` on an unsubscribed store mounts
// and unmounts it at once, so nearly every jsdom file ends with that timer
// pending (catalogStore reads `$catalogs` at import). The cleanup removes the
// `storage`/`pageshow` listeners from `window`; when the worker outlives its
// torn-down jsdom by a second (CI load), `window` is gone and it threw, failing
// a run whose tests all passed. Those listeners went with the destroyed
// window, so removing them then is a no-op. (STORE_UNMOUNT_DELAY cannot be set
// from here: it is an ES module binding, read-only to importers.)
if (typeof window !== "undefined") {
  setPersistentEngine(window.localStorage, {
    addEventListener: windowPersistentEvents.addEventListener,
    removeEventListener(key, listener, restore) {
      if (typeof window === "undefined") return;
      windowPersistentEvents.removeEventListener(key, listener, restore);
    },
  });
}

// jsdom ships no ResizeObserver, and Radix primitives that measure themselves
// (Slider's thumb, among others) throw on mount without it. A no-op observer
// is enough: nothing under test asserts on measured sizes.
if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}
