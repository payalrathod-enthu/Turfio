from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Booking


class Command(BaseCommand):
    help = "Cancels unconfirmed customer requests older than --hours so they stop blocking slots."

    def add_arguments(self, parser):
        parser.add_argument('--hours', type=int, default=24)

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(hours=options['hours'])
        expired = 0
        stale = Booking._base_manager.filter(
            booking_status='pending', created_by__isnull=True, created_at__lt=cutoff)
        for booking in stale:
            if booking.total_received > 0:          # money was recorded, so staff must decide
                continue
            Booking._base_manager.filter(pk=booking.pk).update(
                booking_status='cancelled', cancelled_at=timezone.now(),
                cancellation_reason="Expired: not confirmed in time.")
            expired += 1
        self.stdout.write(self.style.SUCCESS(f"Expired {expired} request(s)."))