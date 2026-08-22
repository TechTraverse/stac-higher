---
name: new-page
description: Create a new page (route) in the STAC Higher UI. Use whenever the user asks to add a page, route, or screen. Covers the Astro thin-shell + React island pattern, QueryProvider/Header wrapping, and route verification.
---

# New Page

## 1. Astro shell

Create the page at the appropriate path under `app/src/pages/`. Each page is a
thin shell that mounts one React island:

```astro
---
import Layout from "../../layouts/Layout.astro";
import { MyPageComponent } from "../../components/.../MyPage";
---
<Layout title="Page Title">
  <MyPageComponent client:only="react" />
</Layout>
```

## 2. React page component

Wrap content in `QueryProvider`, include `Header`:

```tsx
import { QueryProvider } from "@/components/layout/QueryProvider";
import { Header } from "@/components/layout/Header";

function MyPageInner() {
  return (
    <>
      <Header />
      <main className="flex-1 p-6 max-w-6xl mx-auto w-full">
        {/* page content */}
      </main>
    </>
  );
}

export function MyPageComponent() {
  return (
    <QueryProvider>
      <MyPageInner />
    </QueryProvider>
  );
}
```

Everything interactive on the page belongs in this single island — separate
islands can't share React context (QueryClient, form context).

If the page exposes mutations, role-gate the verbs: render read-only for
members, mutation buttons for operator/admin via `useAuthMe()` →
`identity.roles` (see `MonitoringPage.tsx`). Add the nav link in `Header`.

## 3. Verify

- `npm run verify` from the repo root must pass.
- If the dev server is running: `curl -s -o /dev/null -w "%{http_code}" http://localhost:4321/<route>` should return 200. (Something else may own :4321 — an editor's built-in server has squatted it before; confirm the response is actually Astro's before trusting a 200.)
