---
paths:
  - "frontend/**/*.ts"
  - "frontend/**/*.tsx"
---

# Frontend (React) rules

## React best practices (follow when writing frontend code)

1. No magic strings/numbers — extract to named constants (e.g. API paths, status values, colors) at the top of the file or in `constants/`.
2. Memoize expensive renders: `useMemo` for derived lists/filters, `useCallback` for handlers passed to memoized children, `React.memo` sparingly.
3. Don't fetch in render — use `useEffect` with an abort/cleanup flag, and handle loading/error states explicitly.
4. Keep components small: one page per file in `pages/`, extract repeated bits (badges, tables, forms) into `components/`.
5. Derive state instead of syncing it — compute from props/state directly; only use `useState` for genuine UI state (expanded id, form inputs).
6. Avoid defining components inside components; hoist static JSX/config outside the render function.
7. Use stable `key` props (entity ids, never array indexes for dynamic lists).
8. Type props and API responses with interfaces; avoid `any` — use `unknown` + narrowing when the shape is uncertain.
9. Don't put secrets or tokens in source/localStorage beyond what the flow requires; never render full tokens or references (mask them).
10. Prevent unnecessary re-renders: lift state only as high as needed, split large pages into child components.
11. Accessibility: label inputs, use buttons (not clickable divs) for actions, provide alt text.
12. New pages need a route in `routes/index.ts`, a nav entry in `TopNav`, the matching Django `IndexView` path, and a jest spec.
