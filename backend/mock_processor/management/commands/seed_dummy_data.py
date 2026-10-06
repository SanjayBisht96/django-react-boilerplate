import logging
import random

from django.core.management.base import BaseCommand

from mock_processor.models import Charge, PaymentMethod

logger = logging.getLogger(__name__)

# Test card numbers (Luhn-valid)
TEST_CARDS = [
    {"number": "4242424242424242", "expiry": "12/27", "cvv": "123", "brand": "visa"},
    {"number": "5555555555554444", "expiry": "12/27", "cvv": "123", "brand": "mastercard"},
    {"number": "378282246310005", "expiry": "12/27", "cvv": "1234", "brand": "amex"},
    {"number": "6011111111111117", "expiry": "12/27", "cvv": "123", "brand": "discover"},
    # Test value cards (by last 4)
    {"number": "4242424242420002", "expiry": "12/27", "cvv": "123", "brand": "visa"},  # card_declined
    {"number": "4242424242420119", "expiry": "12/27", "cvv": "123", "brand": "visa"},  # processor_error
    {"number": "4242424242420341", "expiry": "12/27", "cvv": "123", "brand": "visa"},  # pending -> succeeded
]

# Test bank accounts
TEST_BANKS = [
    {"account": "000123456789", "routing": "021000021"},  # normal
    {"account": "000123456700", "routing": "021000021"},  # insufficient_funds (0002)
    {"account": "000123456719", "routing": "021000021"},  # processor_error (0119)
    {"account": "000123456741", "routing": "021000021"},  # pending -> succeeded (0341)
]


class Command(BaseCommand):
    help = "Seed dummy payment methods and charges for testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--cards",
            type=int,
            default=7,
            help="Number of card payment methods to create.",
        )
        parser.add_argument(
            "--banks",
            type=int,
            default=4,
            help="Number of bank payment methods to create.",
        )
        parser.add_argument(
            "--charges",
            type=int,
            default=10,
            help="Number of charges to create.",
        )

    def handle(self, *args, **options):
        num_cards = options["cards"]
        num_banks = options["banks"]
        num_charges = options["charges"]

        self.stdout.write("Seeding dummy data...")

        # Create card payment methods
        for i in range(min(num_cards, len(TEST_CARDS))):
            card = TEST_CARDS[i]
            token = f"tok_card_{random.getrandbits(32):08x}"
            PaymentMethod.objects.get_or_create(
                token=token,
                defaults={
                    "method": "card",
                    "last4": card["number"][-4:],
                    "brand_or_bank_type": card["brand"],
                },
            )
            self.stdout.write(f"  Created card: {token} (****{card['number'][-4:]})")

        # Create bank payment methods
        for i in range(min(num_banks, len(TEST_BANKS))):
            bank = TEST_BANKS[i]
            token = f"tok_bank_{random.getrandbits(32):08x}"
            PaymentMethod.objects.get_or_create(
                token=token,
                defaults={
                    "method": "bank",
                    "last4": bank["account"][-4:],
                    "brand_or_bank_type": "checking",
                },
            )
            self.stdout.write(f"  Created bank: {token} (****{bank['account'][-4:]})")

        # Create charges
        payment_methods = list(PaymentMethod.objects.all())
        for i in range(num_charges):
            if not payment_methods:
                break
            pm = random.choice(payment_methods)
            reference = f"pr_{random.getrandbits(64):016x}"
            Charge.objects.get_or_create(
                processor_reference=reference,
                defaults={
                    "payment_method": pm,
                    "amount": random.randint(100, 100000),
                    "currency": "USD",
                    "status": "pending",
                },
            )
            self.stdout.write(f"  Created charge: {reference}")

        self.stdout.write(self.style.SUCCESS("Done!"))
        self.stdout.write(f"  Payment methods: {PaymentMethod.objects.count()}")
        self.stdout.write(f"  Charges: {Charge.objects.count()}")
