/**
 * `runtime.command` (kind 3, container-images spec §3) as a person types it:
 * shell-like words, "shown as the array it becomes" (spec §9.3). It
 * replaces the image's Cmd, never its Entrypoint or User. The limits match
 * the write gate (`commandSchema` in `schemas.ts`), so the form refuses
 * what the route would.
 */
import { MAX_COMMAND_ENTRIES } from "./schemas";

export interface ParsedCommand {
  /** null = no override: the image's own CMD runs. */
  command: string[] | null;
  error: string | null;
}

export function parseCommand(text: string): ParsedCommand {
  const out: string[] = [];
  let current = "";
  let inToken = false;
  let quote: "'" | '"' | null = null;

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quote === "'") {
      if (ch === "'") quote = null;
      else current += ch;
      continue;
    }
    if (quote === '"') {
      if (ch === '"') {
        quote = null;
      } else if (ch === "\\" && (text[i + 1] === '"' || text[i + 1] === "\\")) {
        current += text[i + 1];
        i++;
      } else {
        current += ch;
      }
      continue;
    }
    if (ch === "'" || ch === '"') {
      quote = ch;
      inToken = true;
      continue;
    }
    if (ch === "\\" && i + 1 < text.length) {
      current += text[i + 1];
      i++;
      inToken = true;
      continue;
    }
    if (/\s/.test(ch)) {
      if (inToken) {
        out.push(current);
        current = "";
        inToken = false;
      }
      continue;
    }
    current += ch;
    inToken = true;
  }

  if (quote !== null) {
    return {
      command: null,
      error: `Unterminated ${quote === "'" ? "single" : "double"} quote`,
    };
  }
  if (inToken) out.push(current);
  if (out.length === 0) return { command: null, error: null };
  if (out.some((entry) => entry.trim().length === 0)) {
    return { command: null, error: "Command entries must be non-blank" };
  }
  if (out.length > MAX_COMMAND_ENTRIES) {
    return { command: null, error: `A command carries at most ${MAX_COMMAND_ENTRIES} entries` };
  }
  return { command: out, error: null };
}

const SAFE_WORD = /^[A-Za-z0-9_@%+=:,./-]+$/;

/** The inverse, for syncing a deployed revision's command into the form. */
export function formatCommand(command: readonly string[]): string {
  return command
    .map((entry) => (SAFE_WORD.test(entry) ? entry : `'${entry.replace(/'/g, `'"'"'`)}'`))
    .join(" ");
}
