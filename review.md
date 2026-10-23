# Payment Gateway — Code Review: Concurrency & Postgres

**Date:** 2026-10-24  
**Scope:** `backend/payments/`, `backend/mock_processor/`, `backend/payment_gateway/settings/`  
**Reviewer:** Automated code review (concurrency + Postgres focus)

---

## Summary

The payment gateway design is solid overall — it uses an append-only ledger, idempotency keys, `select_for_update()` row locking, a transactional outbox pattern, and a state machine. However, **several critical concurrency bugs and Postgres query issues** can lead to duplicate payments, lost updates, stuck payments, and inconsistent state under load.

---

## 1. Create Payment — Idempotency Race Conditions

### 1a. TOCTOU between Redis inflight marker and DB write

**Location:** `backend/payments/views.py` — `create_payment()`

**Description:** The idempotency flow checks Redis `idem:inflight:{key}` (SET NX), then checks the DB `Payment.objects.filter(idempotency_key=...)`, then creates inside `transaction.atomic()`. Two concurrent requests with the same idempotency key can both pass the Redis NX check (one sets it, the other gets `None` and proceeds), both find no existing payment in the DB, and both attempt to create. The DB `UniqueConstraint` on `(idempotency_key, idempotency_body_hash)` catches this only if the body hash matches. If the body hash differs, one succeeds and the other raises an **uncaught `IntegrityError`**, resulting in a 500 error instead of a clean 409.

**Impact:** Duplicate payment creation attempts; 500 errors on conflicting idempotency keys; lost idempotency guarantees under concurrent retries.

**Solution:**
- Wrap the `Payment.objects.create()` in `try/except IntegrityError` and return 409 when the constraint is violated.
- Re-fetch the existing payment after the integrity error and return its serialized data if the body hash matches, or 409 if it differs.
- Treat Redis as an optimization only, not a lock — the DB unique constraint is the source of truth.

```python
try:
    with transaction.atomic():
        payment = Payment.objects.create(...)
except IntegrityError:
    existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
    if existing and existing.idempotency_body_hash == body_hash:
        return Response(PaymentSerializer(existing).data, status=200)
    return Response({"error": "Idempotency-Key already used with a different request body"}, status=409)
```

---

### 1b. Redis cached result can return stale/missing payment

**Location:** `backend/payments/views.py` — `create_payment()` Redis fast-path

**Description:** If Redis returns a cached payment ID but the payment was deleted or the hash doesn't match, the code falls through `except Payment.DoesNotExist: pass` and proceeds to create a *new* payment with the same idempotency key, which then hits the DB unique constraint.

**Impact:** Path to duplicate payments when Redis cache is stale or payment rows are missing.

**Solution:** When the cached payment is not found or hash mismatches, delete the stale Redis key and fall through to the DB idempotency check rather than silently creating a new payment.

---

### 1c. `payment.save()` after processor call is outside the transaction

**Location:** `backend/payments/views.py` — `create_payment()` processor call block

**Description:** After `requests.post()` to the processor succeeds, the code updates `payment.processor_reference` and `payment.status` **outside** the `transaction.atomic()` block that created the payment. If the process crashes after the processor call but before `save()`, the payment stays in `CREATED` state forever with no `pending` ledger entry — a stuck payment with no automatic recovery path.

**Impact:** Stuck payments in `CREATED` state; requires manual admin replay to recover.

**Solution:** Move the processor call and subsequent `save()` inside the `transaction.atomic()` block, or use `transaction.on_commit()` to enqueue the processor call after the transaction commits. Alternatively, create the payment in `PENDING` state directly and let the processor call update it within the same transaction.

---

## 2. Webhook Processing — Locking Gaps

### 2a. `select_for_update()` called AFTER the initial unlocked `get()`

**Location:** `backend/payments/views.py` — `processor_webhook()`

**Description:**
```python
payment = Payment.objects.get(processor_reference=processor_reference)  # NO LOCK
# ...
with transaction.atomic():
    payment = Payment.objects.select_for_update().get(id=payment.id)
```

Between the first `get()` (no lock) and the `select_for_update()`, another transaction can modify or lock the payment row. The duplicate ledger check runs inside the lock, which is good, but the initial lookup is unprotected.

**Impact:** Two concurrent webhook deliveries for the same payment can both pass the initial lookup before either acquires the lock, leading to redundant processing and potential duplicate ledger entries (though the `unique_together` constraint prevents exact duplicates).

**Solution:** Use `select_for_update()` on the initial lookup:
```python
payment = Payment.objects.select_for_update().get(processor_reference=processor_reference)
```

---

### 2b. No `nowait`/`skip_locked` on webhook lock

**Location:** `backend/payments/views.py` — `processor_webhook()`

**Description:** Under concurrent webhook delivery (e.g., `simulate_webhooks --mode concurrent`), two threads block on `select_for_update()`. One holds the lock, processes, commits. The other then proceeds, sees the duplicate `event_id`, and returns 200. This serializes all webhooks for the same payment and becomes a bottleneck under high throughput.

**Impact:** Webhook processing serialized per payment; increased latency under concurrent webhook storms.

**Solution:** Use `select_for_update(nowait=True)` with a retry loop, or `skip_locked=True` to process non-contended payments first and retry contended ones later.

---

### 2c. State machine allows `pending → pending` self-transition

**Location:** `backend/payments/state_machine.py` — `_TRANSITIONS`

**Description:**
```python
"pending": {Payment.Status.PENDING, Payment.Status.SUCCEEDED, Payment.Status.FAILED, Payment.Status.AUTHORIZED},
```

A `pending → pending` transition is allowed, so a duplicate `pending` webhook with a *different* `event_id` creates a second ledger entry with the same status. The `unique_together` on `(payment, event_id)` prevents exact duplicates but not same-status-different-event_id entries.

**Impact:** Ledger pollution with redundant `pending` entries; confusing payment history.

**Solution:** Remove `Payment.Status.PENDING` from its own allowed transitions. `PENDING` should only transition forward to `AUTHORIZED`, `SUCCEEDED`, or `FAILED`.

---

## 3. Ledger Entry — Append-Only Enforcement is Race-Prone

**Location:** `backend/payments/models.py` — `LedgerEntry.save()` and `LedgerEntry.delete()`

**Description:** The Python-level overrides check `if self.pk is not None and self.__class__.objects.filter(pk=self.pk).exists()` before raising. This is a **check-then-act** race. Two concurrent saves of the same object could both pass. The real safety net is the DB-level `unique_together = ("payment", "event_id")` constraint.

**Impact:** The Python overrides are defense-in-depth only; they do not prevent concurrent modifications. The DB constraint is what actually enforces append-only.

**Solution:** Keep the DB-level `unique_together` constraint (already present). The Python `save()`/`delete()` overrides are acceptable as additional defense but should not be relied upon. Consider adding a database trigger or check constraint for stronger enforcement if needed.

---

## 4. Outbox Pattern — `delivered` Flag Race

**Location:** `backend/payments/management/commands/publish_outbox.py`

**Description:**
```python
events = OutboxEvent.objects.filter(delivered=False).order_by("created")[:100]
for event in events:
    producer.send("payment_events", value={...})
    event.delivered = True
    event.save(update_fields=["delivered"])
```

If two publisher workers run concurrently, both can fetch the same undelivered events, both send to Kafka, and both mark `delivered=True`. This causes **duplicate message delivery** to Kafka consumers.

**Impact:** Duplicate email notifications sent to customers; duplicate downstream processing.

**Solution:** Atomically claim events using `select_for_update(nowait=True)` or an `UPDATE ... SET delivered = TRUE WHERE delivered = FALSE RETURNING *` pattern:
```python
events = OutboxEvent.objects.select_for_update(nowait=True).filter(delivered=False)[:100]
```
Or use `update(delivered=True)` with a filter on `delivered=False` to atomically claim rows before sending.

---

## 5. State Machine — Missing Transitions & Terminal Logic

### 5a. `CREATED` is not terminal but has no clear exit path except via webhook

**Location:** `backend/payments/state_machine.py` — `_TERMINAL` and `_TRANSITIONS`

**Description:** `CREATED` can transition to `AUTHORIZED`, `FAILED`, or `PENDING`, but it is not in `_TERMINAL`. After `create_payment`, the payment goes `CREATED → PENDING` via the processor call. If the processor call fails, it goes `CREATED → FAILED`. This works, but the state machine does not explicitly model the `CREATED → PENDING` transition initiated by the create flow.

**Impact:** Unclear state machine semantics; potential for payments stuck in `CREATED` if the processor call fails silently.

**Solution:** Document the `CREATED → PENDING` transition explicitly. Ensure `CREATED` payments that fail processor charging transition to `FAILED` with a failure code.

---

### 5b. `can_transition()` silently returns `False` for unknown statuses

**Location:** `backend/payments/state_machine.py` — `can_transition()`

**Description:**
```python
allowed = _TRANSITIONS.get(current_status, set())
return new_status in allowed
```

If `current_status` is unknown (e.g., corrupted data), it returns `False` silently. No warning is logged.

**Impact:** Silent rejection of transitions for corrupted or unknown statuses; difficult to debug.

**Solution:** Log a warning when `current_status` is not found in `_TRANSITIONS`.

---

## 6. Postgres Queries & Missing Indexes

### 6a. Missing index on `Payment.processor_reference`

**Location:** `backend/payments/models.py` — `Payment.processor_reference`

**Description:** The webhook endpoint looks up payments by `processor_reference`:
```python
payment = Payment.objects.get(processor_reference=processor_reference)
```
There is no `db_index=True` on this field. Under high webhook volume, this is a sequential scan on the `payments_payment` table.

**Impact:** Slow webhook lookups as the payments table grows; increased DB CPU under webhook load.

**Solution:** Add `db_index=True` to `Payment.processor_reference`.

---

### 6b. Missing standalone index on `Payment.idempotency_key`

**Location:** `backend/payments/models.py` — `Payment.idempotency_key`

**Description:** The idempotency flow queries `Payment.objects.filter(idempotency_key=...)`. The field is only covered by the composite `UniqueConstraint` on `(idempotency_key, idempotency_body_hash)`. Queries filtering by `idempotency_key` alone cannot efficiently use the composite index.

**Impact:** Slower idempotency lookups under concurrent payment creation.

**Solution:** Add `db_index=True` to `Payment.idempotency_key` (or a separate index).

---

### 6c. Missing composite index on `OutboxEvent(delivered, created)`

**Location:** `backend/payments/models.py` — `OutboxEvent`

**Description:** The publisher queries `filter(delivered=False).order_by("created")`. There is a `db_index=True` on `delivered` and `created` separately, but no composite index on `(delivered, created)`.

**Impact:** Suboptimal query plan for the outbox publisher; Postgres may scan all undelivered rows then sort, or use one index and filter on the other.

**Solution:** Add a composite index: `models.Index(fields=["delivered", "created"])`.

---

### 6d. `ATOMIC_REQUESTS` not explicitly set

**Location:** `backend/payment_gateway/settings/base.py`

**Description:** The README mentions `DATABASES["default"]["ATOMIC_REQUESTS"] = True`, but it is not explicitly set in `base.py`. Without it, each view runs in autocommit mode, and only explicit `transaction.atomic()` blocks provide transaction boundaries.

**Impact:** If any mutating code path forgets `transaction.atomic()`, partial writes can occur. The current code uses `atomic()` in the right places, but this is a fragile reliance on developer discipline.

**Solution:** Explicitly set `ATOMIC_REQUESTS = True` in `DATABASES["default"]` in `base.py`, and review all mutating views to ensure they are within atomic blocks.

---

## 7. Other Concurrency Issues

### 7a. `replay_payment` — no locking, no idempotency

**Location:** `backend/payments/views.py` — `replay_payment()`

**Description:**
```python
payment = Payment.objects.get(id=payment_id)
# ... processor call ...
payment.processor_reference = new_reference
payment.save()
```

No `select_for_update()`, no check that the payment isn't already terminal. Two concurrent admin replays of the same stuck payment could both update `processor_reference`, with the second save overwriting the first (lost update). Both could also create duplicate charges at the processor.

**Impact:** Lost updates on `processor_reference`; duplicate processor charges; inconsistent payment state.

**Solution:** Add `select_for_update()` to lock the payment row. Check that the payment is not in a terminal state before replaying. Consider idempotency for replay requests.

---

### 7b. `send_payment_email` Celery task — no idempotency

**Location:** `backend/payments/tasks.py` — `send_payment_email()`

**Description:** The task is dispatched per `OutboxEvent`. If the same event is delivered twice (due to the outbox `delivered` race in issue #4), two emails are sent. The task has `max_retries=3` but no deduplication.

**Impact:** Duplicate emails sent to customers.

**Solution:** Deduplicate email tasks by outbox event ID or payment ID + event type. Store a record of sent emails and skip if already sent.

---

### 7c. `consume_payment_events` — Kafka auto-commit can lose messages

**Location:** `backend/payments/management/commands/consume_payment_events.py`

**Description:**
```python
enable_auto_commit=True
```
If the Celery task dispatch succeeds but the consumer crashes before processing, the offset is already committed and the event is lost.

**Impact:** Lost payment events; emails never sent for some payments.

**Solution:** Disable auto-commit and manually commit offsets after successful Celery task dispatch.

---

## Recommended Fix Priority

| Priority | Issue | Location |
|----------|-------|----------|
| Critical | Outbox `delivered` flag race | `publish_outbox.py` |
| Critical | Webhook initial `get()` without lock | `views.py` `processor_webhook()` |
| Critical | Idempotency create TOCTOU / uncaught `IntegrityError` | `views.py` `create_payment()` |
| High | `payment.save()` outside transaction after processor call | `views.py` `create_payment()` |
| High | `replay_payment` lost update / no locking | `views.py` `replay_payment()` |
| High | Missing index on `processor_reference` | `models.py` `Payment` |
| Medium | Missing index on `idempotency_key` | `models.py` `Payment` |
| Medium | Missing composite index on `OutboxEvent(delivered, created)` | `models.py` `OutboxEvent` |
| Medium | `pending → pending` self-transition | `state_machine.py` |
| Medium | Kafka auto-commit message loss | `consume_payment_events.py` |
| Low | `send_payment_email` no idempotency | `tasks.py` |
| Low | `ATOMIC_REQUESTS` not explicitly set | `base.py` |

---

## Testing Notes

- Concurrency tests in `backend/payments/tests/test_concurrency.py` use `TransactionTestCase` with threading, which is appropriate for testing `select_for_update()` behavior.
- These tests require PostgreSQL (SQLite does not support concurrent writes / `select_for_update()`). The `@skipIf(IS_SQLITE, ...)` decorators are commented out — ensure tests run against PostgreSQL in CI.
- The `simulate_webhooks --mode concurrent` management command is useful for reproducing webhook race conditions locally.
