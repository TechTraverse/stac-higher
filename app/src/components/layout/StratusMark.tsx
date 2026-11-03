import type { SVGProps } from "react";

/** Brand name and tagline, in one place so the shell never drifts. */
export const BRAND_NAME = "Stratus";
export const BRAND_TAGLINE = "Cover more ground";

/**
 * The Stratus mark: three offset, flat cloud layers — a stratus deck seen
 * edge-on. Drawn on a 24px grid with `currentColor` strokes so it sits in
 * the same slots as a lucide icon. `public/favicon.svg` is the same shape.
 */
export function StratusMark(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.25}
      strokeLinecap="round"
      aria-hidden="true"
      {...props}
    >
      <path d="M7 7.5h11" />
      <path d="M3.5 12h13" opacity={0.85} />
      <path d="M8.5 16.5h12" opacity={0.7} />
    </svg>
  );
}
