from django.core.management.base import BaseCommand

from celery import chain

from credit.tasks import ingest_customers_from_excel, ingest_loans_from_excel


class Command(BaseCommand):
    help = "Enqueue background ingestion of customers and loans from Excel files."

    def handle(self, *args, **options):
        workflow = chain(
            ingest_customers_from_excel.si(),
            ingest_loans_from_excel.si(),
        )
        result = workflow.apply_async()
        self.stdout.write(self.style.SUCCESS(f"Enqueued ingestion chain: {result.id}"))
