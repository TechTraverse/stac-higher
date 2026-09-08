/**
 * Guard hooks for opencode — the harness-neutral equivalents of the two
 * Claude Code hooks in `.claude/settings.json`.
 *
 * Auto-discovered by opencode from `.opencode/plugin/`; no `opencode.json`
 * entry needed. Plain JS on purpose — typing it would mean adding
 * `@opencode-ai/plugin` to the root devDependencies, and AGENTS.md forbids
 * new dependencies without clear need.
 *
 * 1. shadcn guard  — refuses `edit`/`write` to any `components/ui/` file.
 *                    AGENTS.md: use `npx shadcn@latest add <component>`.
 * 2. astro check   — after a `.ts`/`.tsx`/`.astro` edit under `app/` or
 *                    `packages/shared/`, runs the app-scoped type check and
 *                    appends failures to the tool result, so the model sees
 *                    them immediately instead of at `npm run verify`.
 *
 * Both apply to subagents as well as the primary agent.
 *
 * @typedef {{ tool: string, sessionID: string, callID: string, args?: any }} ToolInput
 * @typedef {{ title: string, output: string, metadata: any }} ToolOutput
 */

import { existsSync } from "node:fs"
import { join } from "node:path"

/** Tools that write files. Both put the target in `args.filePath`. */
const WRITE_TOOLS = new Set(["edit", "write"])

/** shadcn primitives — hand-edits are refused in both harnesses. */
const UI_PRIMITIVE = /(?:^|\/)components\/ui\//

/** Files the app-scoped `astro check` actually understands. */
const CHECKABLE = /\.(?:ts|tsx|astro)$/

/**
 * Milliseconds before the check is abandoned. Mirrors the 30s timeout on the
 * Claude Code PostToolUse hook.
 */
const CHECK_TIMEOUT_MS = 30_000

/** How many trailing lines of check output to surface. */
const CHECK_TAIL_LINES = 15

/**
 * Resolve the checkout root from the edited file itself rather than from the
 * plugin's own `worktree`. Edits frequently land in `.claude/worktrees/<slug>/`
 * while opencode is rooted at the main checkout — running the check in the
 * wrong `app/` would type-check code that was never touched.
 *
 * @param {string} filePath absolute path of the edited file
 * @returns {string | undefined} the checkout root, if the path is in a checked scope
 */
function checkoutRootFor(filePath) {
  const match = filePath.match(/^(.*?)\/(?:app|packages\/shared)\//)
  const root = match?.[1]
  if (!root) return undefined
  // Guard against a coincidental `app/` segment outside a real checkout.
  return existsSync(join(root, "app", "package.json")) ? root : undefined
}

/** @param {string} text */
function tail(text) {
  const lines = text.trimEnd().split("\n")
  return lines.slice(-CHECK_TAIL_LINES).join("\n")
}

/** @type {(input: { $: any, worktree: string, directory: string }) => Promise<any>} */
export const GuardsPlugin = async ({ $ }) => {
  return {
    /**
     * @param {ToolInput} input
     * @param {{ args: any }} output
     */
    "tool.execute.before": async (input, output) => {
      if (!WRITE_TOOLS.has(input.tool)) return
      const filePath = output.args?.filePath
      if (typeof filePath !== "string") return
      if (!UI_PRIMITIVE.test(filePath)) return

      throw new Error(
        "Refused: never hand-edit shadcn/ui primitives (AGENTS.md). " +
          `Use \`npx shadcn@latest add <component>\` instead of editing ${filePath}. ` +
          "If the app consumes the component from `@stac-higher/shared`, add it to " +
          "`packages/shared/` rather than `app/src/components/ui/`.",
      )
    },

    /**
     * @param {ToolInput} input
     * @param {ToolOutput} output
     */
    "tool.execute.after": async (input, output) => {
      if (!WRITE_TOOLS.has(input.tool)) return
      const filePath = input.args?.filePath
      if (typeof filePath !== "string") return
      if (!CHECKABLE.test(filePath)) return

      const root = checkoutRootFor(filePath)
      if (!root) return

      let result
      try {
        result = await Promise.race([
          $`npx astro check --minimumSeverity error`
            .cwd(join(root, "app"))
            .nothrow()
            .quiet(),
          new Promise((resolve) =>
            setTimeout(() => resolve({ timedOut: true }), CHECK_TIMEOUT_MS),
          ),
        ])
      } catch (error) {
        // A broken check must never break the edit that triggered it.
        output.output += `\n\n[astro check] could not run: ${error instanceof Error ? error.message : String(error)}`
        return
      }

      if (result?.timedOut) {
        output.output += `\n\n[astro check] timed out after ${CHECK_TIMEOUT_MS / 1000}s — run \`npm run check\` from \`app/\` manually.`
        return
      }

      if (result.exitCode === 0) return

      const stdout = result.stdout?.toString() ?? ""
      const stderr = result.stderr?.toString() ?? ""
      const combined = `${stdout}${stderr}`.trim()

      output.output +=
        `\n\n[astro check] FAILED (exit ${result.exitCode}) — fix these before continuing:\n` +
        (combined ? tail(combined) : "(no output captured)")
    },
  }
}

export default GuardsPlugin
