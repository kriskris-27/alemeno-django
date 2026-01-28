from decimal import Decimal
from typing import Tuple

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Customer, Loan
from .serializers import (
    CheckEligibilityRequestSerializer,
    CustomerSerializer,
    RegisterCustomerSerializer,
)


class RegisterView(APIView):
    def post(self, request):
        serializer = RegisterCustomerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Generate next external customer_id
        next_id = (Customer.objects.order_by("-customer_id").first().customer_id + 1) if Customer.objects.exists() else 1

        monthly_income = Decimal(data["monthly_income"])
        approved_limit = round((monthly_income * 36) / Decimal("100000")) * Decimal("100000")

        with transaction.atomic():
            customer = Customer.objects.create(
                customer_id=next_id,
                first_name=data["first_name"],
                last_name=data["last_name"],
                age=data.get("age"),
                phone_number=data["phone_number"],
                monthly_salary=monthly_income,
                approved_limit=approved_limit,
                current_debt=Decimal("0"),
            )

        resp = {
            "customer_id": customer.customer_id,
            "name": f"{customer.first_name} {customer.last_name}",
            "age": customer.age,
            "monthly_income": int(monthly_income),
            "approved_limit": int(approved_limit),
            "phone_number": customer.phone_number,
        }
        return Response(resp, status=status.HTTP_201_CREATED)


def _compute_emi(principal: Decimal, annual_rate: Decimal, tenure_months: int) -> Decimal:
    r = (annual_rate / Decimal("100")) / Decimal("12")
    if r == 0:
        return (principal / tenure_months).quantize(Decimal("0.01"))
    numerator = principal * r * (1 + r) ** tenure_months
    denominator = (1 + r) ** tenure_months - 1
    emi = numerator / denominator
    return emi.quantize(Decimal("0.01"))


def _compute_credit_score(customer: Customer, loans) -> Tuple[int, Decimal, Decimal]:
    today = timezone.now().date()
    active_loans = loans.filter(end_date__gte=today)
    total_active_amount = sum((loan.loan_amount for loan in active_loans), Decimal("0"))
    total_active_emi = sum((loan.monthly_repayment for loan in active_loans), Decimal("0"))

    if total_active_amount > customer.approved_limit:
        return 0, total_active_amount, total_active_emi

    total_loans = loans.count()
    total_amount = sum((loan.loan_amount for loan in loans), Decimal("0"))
    avg_paid_ratio = 1
    if total_loans:
        ratios = []
        for loan in loans:
            if loan.tenure:
                ratios.append(min(Decimal(loan.emis_paid_on_time) / Decimal(loan.tenure), Decimal("1")))
        avg_paid_ratio = sum(ratios, Decimal("0")) / len(ratios) if ratios else Decimal("1")

    paid_score = (avg_paid_ratio * 30)
    loan_count_score = min(total_loans, 5) / Decimal("5") * Decimal("20")

    recent = loans.filter(start_date__year=today.year).count()
    recent_score = max(Decimal("0"), Decimal("20") - Decimal(max(0, recent - 1)) * Decimal("5"))

    volume_ratio = (total_amount / customer.approved_limit) if customer.approved_limit else Decimal("1")
    volume_score = max(Decimal("0"), Decimal("20") * (Decimal("1") - min(volume_ratio, Decimal("1"))))

    base_score = Decimal("10")

    score = paid_score + loan_count_score + recent_score + volume_score + base_score
    score = int(max(0, min(100, score)))
    return score, total_active_amount, total_active_emi


class CheckEligibilityView(APIView):
    def post(self, request):
        serializer = CheckEligibilityRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            customer = Customer.objects.get(customer_id=data["customer_id"])
        except Customer.DoesNotExist:
            return Response({"detail": "Customer not found."}, status=status.HTTP_404_NOT_FOUND)

        loans = Loan.objects.filter(customer=customer)
        score, active_amount, active_emi = _compute_credit_score(customer, loans)

        loan_amount = Decimal(data["loan_amount"])
        requested_rate = Decimal(data["interest_rate"])
        tenure = int(data["tenure"])

        # If score is zero due to limits
        if score == 0:
            return Response(
                {
                    "customer_id": customer.customer_id,
                    "approval": False,
                    "interest_rate": float(requested_rate),
                    "corrected_interest_rate": None,
                    "tenure": tenure,
                    "monthly_installment": None,
                    "reason": "credit score zero (active loans exceed limit)",
                },
                status=status.HTTP_200_OK,
            )

        # Determine slab
        if score > 50:
            min_rate = requested_rate
        elif score > 30:
            min_rate = Decimal("12")
        elif score > 10:
            min_rate = Decimal("16")
        else:
            return Response(
                {
                    "customer_id": customer.customer_id,
                    "approval": False,
                    "interest_rate": float(requested_rate),
                    "corrected_interest_rate": None,
                    "tenure": tenure,
                    "monthly_installment": None,
                    "reason": "credit score too low",
                },
                status=status.HTTP_200_OK,
            )

        corrected_rate = max(requested_rate, min_rate)
        monthly_installment = _compute_emi(loan_amount, corrected_rate, tenure)

        # EMI constraint: total EMIs should not exceed 50% of salary
        total_emi = active_emi + monthly_installment
        if total_emi > (customer.monthly_salary * Decimal("0.5")):
            return Response(
                {
                    "customer_id": customer.customer_id,
                    "approval": False,
                    "interest_rate": float(requested_rate),
                    "corrected_interest_rate": float(corrected_rate),
                    "tenure": tenure,
                    "monthly_installment": float(monthly_installment),
                    "reason": "emi exceeds 50% of monthly salary",
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "customer_id": customer.customer_id,
                "approval": True,
                "interest_rate": float(requested_rate),
                "corrected_interest_rate": float(corrected_rate),
                "tenure": tenure,
                "monthly_installment": float(monthly_installment),
            },
            status=status.HTTP_200_OK,
        )
    
from rest_framework import viewsets
from .models import Customer
from .serializers import CustomerSerializer

class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
