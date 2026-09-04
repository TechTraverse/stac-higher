import "@testing-library/jest-dom/vitest";

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
