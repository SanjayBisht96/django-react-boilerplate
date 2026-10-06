---
paths:
  - "backend/**/*.py"
---

# Backend (Django) rules

## Django best practices (follow when writing backend code)

1. No magic strings/numbers — use model `TextChoices`/`IntegerChoices`, settings constants, or named constants (e.g. `Payment.Status.PENDING`, not `"pending"`).
2. Avoid N+1 queries — use `select_related()` for FK/one-to-one and `prefetch_related()` for reverse/many relations when serializing collections.
3. Use `get_object_or_404()` / `get_list_or_404()` instead of manual try/except on `DoesNotExist` in views.
4. Keep business logic out of views — services/helpers (`payments/state_machine.py`, `_charge_token`) so they're testable without HTTP.
5. Never perform multi-row writes without `transaction.atomic()`; use `select_for_update()` for read-modify-write races.
6. Don't put secrets or environment-specific values in code — read via `decouple.config()` with safe defaults.
7. Validate at the boundary with serializers; don't trust `request.data` types directly in model creation.
8. Use `update_fields` on `save()` for partial updates, especially on append-only-adjacent records.
9. Add `db_index=True` on fields used in filters/lookups (status, event_id, idempotency_key).
10. Don't swallow exceptions — log with `logger.exception`/`logger.warning` and return a proper HTTP status.
11. Prefer `F()` expressions / `Count` annotations over Python-side aggregation loops.
12. New endpoints must have `@extend_schema` docs and at least one test.
