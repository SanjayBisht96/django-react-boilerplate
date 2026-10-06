"""Reconciliation worker: match bank settlement file to local payments.

Expected file format (CSV with header):
    processor_reference,amount,currency,settled_at

Payments in CAPTURED status found in the file transition to SETTLED.
Mismatches are reported, never silently mutated.
"""

import csv
from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from payments.models import LedgerEntry, OutboxEvent, Payment


class Command(BaseCommand):
    help = "Reconcile bank settlement file and mark settled payments."

    def add_arguments(self, parser):
        parser.add_argument("--file", required=True)

    def handle(self, *args, **options):
        path = options["file"]
        try:
            rows = list(csv.DictReader(open(path, newline="")))
        except (OSError, csv.Error) as e:
            raise CommandError(f"Cannot read settlement file: {e}")

        by_ref = defaultdict(list)
        for row in rows:
            ref = (row.get("processor_reference") or "").strip()
            if ref:
                by_ref[ref].append(row)

        settled, flagged, missing = 0, 0, 0
        local_refs = set(
            Payment.objects.filter(
                status__in=[Payment.Status.CAPTURED, Payment.Status.AUTHORIZED]
            ).values_list("processor_reference", flat=True)
        )

        for ref, ref_rows in by_ref.items():
            try:
                payment = Payment.objects.get(processor_reference=ref)
            except Payment.DoesNotExist:
                missing += 1
                self.stderr.write(f"MISSING locally: {ref}")
                continue

            row = ref_rows[0]
            try:
                file_amount = int(row["amount"])
            except (KeyError, ValueError):
                flagged += 1
                self.stderr.write(f"BAD AMOUNT in file for {ref}")
                continue

            if file_amount != payment.amount:
                flagged += 1
                self.stderr.write(
                    f"MISMATCH {ref}: file={file_amount} local={payment.amount}"
                )
                continue

            if payment.status == Payment.Status.SETTLED:
                continue
            if LedgerEntry.objects.filter(payment=payment, event_id=f"settle_{ref}").exists():
                continue

            with transaction.atomic():
                payment = Payment.objects.select_for_update().get(id=payment.id)
                payment.status = Payment.Status.SETTLED
                payment.save(update_fields=["status", "modified"])
                LedgerEntry.objects.create(
                    payment=payment,
                    event_id=f"settle_{ref}",
                    status=Payment.Status.SETTLED,
                    occurred_at=timezone.now(),
                )
                OutboxEvent.objects.create(
                    payment=payment,
                    event_type="payment.settled",
                    payload={
                        "payment_id": str(payment.id),
                        "processor_reference": ref,
                        "amount": payment.amount,
                        "currency": payment.currency,
                        "customer_email": payment.customer_email,
                        "status": "settled",
                    },
                )
            settled += 1

        not_in_file = local_refs - set(by_ref.keys())
        for ref in sorted(not_in_file):
            self.stderr.write(f"NOT SETTLED in file: {ref}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciliation done: settled={settled} flagged={flagged} missing_locally={missing} not_in_file={len(not_in_file)}"
            )
        )
