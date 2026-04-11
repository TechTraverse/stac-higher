Create a new page in the STAC Higher UI. The page route/name is: $ARGUMENTS

Follow these steps:

1. Create the Astro page file at the appropriate path under `src/pages/`. Follow the existing pattern — each page is a thin shell:

```astro
---
import Layout from "../../layouts/Layout.astro";
import { MyPageComponent } from "../../components/.../MyPage";
---
<Layout title="Page Title">
  <MyPageComponent client:only="react" />
</Layout>
```

2. Create the React page component. Follow the existing pattern — wrap content in `QueryProvider`, include `Header`:

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

3. Run `cd $CLAUDE_PROJECT_DIR/app && npx astro check` to verify 0 errors.

4. Test the route: `curl -s -o /dev/null -w "%{http_code}" http://localhost:4321/<route>` (if dev server is running).
