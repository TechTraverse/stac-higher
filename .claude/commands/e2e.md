Run the Playwright e2e test suite non-interactively. Optional spec filter: $ARGUMENTS

Steps:

1. Ensure the Docker Compose backend is running. Check with `docker compose ps`; if `pgstac` or `stac-fastapi` are not `Up`, start them with `cd $CLAUDE_PROJECT_DIR && docker compose up -d` and wait a few seconds for healthy state. `extensions.spec.ts` and `proxy.spec.ts` depend on this.

2. Run the tests with the `list` reporter so output streams line-by-line and no HTML report / browser window is opened:

   - No filter: `cd $CLAUDE_PROJECT_DIR/app && npm run test:e2e:ci`
   - With filter: `cd $CLAUDE_PROJECT_DIR/app && npm run test:e2e:ci -- $ARGUMENTS`

   The Playwright config auto-starts a dev server on :4321 if one is not already running, and reuses an existing one if it is.

3. Report a concise summary: total pass/fail counts. On failure, list only the failing test names and the first error line for each. Do not dump the full report directory.
