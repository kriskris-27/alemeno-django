import logging
from decimal import Decimal
from pathlib import Path
from typing import Dict, List

import pandas as pd
from celery import shared_task
from django.conf import settings
from django.db import transaction

from .models import Customer, Loan

logger = logging.getLogger(__name__)


def _data_dir() -> Path:
    return Path(getattr(settings, "DATA_DIR", settings.BASE_DIR / "data"))


def _load_excel(filename: str) -> pd.DataFrame:
    path = _data_dir() / filename
    if not path.exists():
        logger.error("File not found: %s", path)
        return pd.DataFrame()
    df = pd.read_excel(path)
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return df


def _decimal(value) -> Decimal:
    return Decimal(str(value))


@shared_task
def ingest_customers_from_excel(filename: str = "customer_data.xlsx") -> Dict[str, int]:
    df = _load_excel(filename)
    required = {"customer_id", "first_name", "last_name", "phone_number", "monthly_salary", "approved_limit", "current_debt"}
    missing = required - set(df.columns)
    if missing:
        logger.error("Missing columns in %s: %s", filename, missing)
        return {"created": 0, "skipped": len(df)}

    customers: List[Customer] = []
    skipped = 0
    for _, row in df.iterrows():
        if any(pd.isna(row[col]) for col in ("customer_id", "first_name", "last_name")):
            skipped += 1
            continue
        try:
            customers.append(
                Customer(
                    customer_id=int(row["customer_id"]),
                    first_name=str(row["first_name"]),
                    last_name=str(row["last_name"]),
                    phone_number=str(row.get("phone_number", "")),
                    monthly_salary=_decimal(row["monthly_salary"]),
                    approved_limit=_decimal(row["approved_limit"]),
                    current_debt=_decimal(row.get("current_debt", 0)),
                )
            )
        except Exception as exc:  # pragma: no cover - log and skip bad rows
            skipped += 1
            logger.warning("Skipping customer row due to error: %s", exc)

    with transaction.atomic():
        created = len(
            Customer.objects.bulk_create(
                customers, ignore_conflicts=True, batch_size=500
            )
        )
    logger.info("Customers ingested: created=%s skipped=%s", created, skipped)
    return {"created": created, "skipped": skipped}


@shared_task
def ingest_loans_from_excel(filename: str = "loan_data.xlsx") -> Dict[str, int]:
    df = _load_excel(filename)
    required = {
        "customer_id",
        "loan_id",
        "loan_amount",
        "tenure",
        "interest_rate",
        "monthly_repayment",
        "emis_paid_on_time",
        "start_date",
        "end_date",
    }
    missing = required - set(df.columns)
    if missing:
        logger.error("Missing columns in %s: %s", filename, missing)
        return {"created": 0, "skipped": len(df)}

    customers_map = Customer.objects.in_bulk(field_name="customer_id")
    loans: List[Loan] = []
    skipped = 0
    for _, row in df.iterrows():
        customer_id = int(row["customer_id"]) if not pd.isna(row["customer_id"]) else None
        if customer_id is None or customer_id not in customers_map:
            skipped += 1
            continue
        try:
            loans.append(
                Loan(
                    loan_id=int(row["loan_id"]),
                    customer=customers_map[customer_id],
                    loan_amount=_decimal(row["loan_amount"]),
                    tenure=int(row["tenure"]),
                    interest_rate=_decimal(row["interest_rate"]),
                    monthly_repayment=_decimal(row["monthly_repayment"]),
                    emis_paid_on_time=int(row.get("emis_paid_on_time", 0)),
                    start_date=pd.to_datetime(row["start_date"]).date(),
                    end_date=pd.to_datetime(row["end_date"]).date(),
                )
            )
        except Exception as exc:  # pragma: no cover
            skipped += 1
            logger.warning("Skipping loan row due to error: %s", exc)

    with transaction.atomic():
        created = len(
            Loan.objects.bulk_create(
                loans, ignore_conflicts=True, batch_size=500
            )
        )
    logger.info("Loans ingested: created=%s skipped=%s", created, skipped)
    return {"created": created, "skipped": skipped}
