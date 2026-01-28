from decimal import Decimal
from typing import Tuple
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Customer, Loan
from .serializers import (
    CheckEligibilityRequestSerializer,
    CreateLoanRequestSerializer,
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
        decision = evaluate_loan_request(
            customer=customer,
            loans=loans,
            loan_amount=Decimal(data["loan_amount"]),
            requested_rate=Decimal(data["interest_rate"]),
            tenure=int(data["tenure"]),
        )
        if not decision["approval"]:
            return Response(
                {
                    "customer_id": customer.customer_id,
                    "approval": False,
                    "interest_rate": float(decision["requested_rate"]),
                    "corrected_interest_rate": float(decision["corrected_rate"]) if decision["corrected_rate"] else None,
                    "tenure": decision["tenure"],
                    "monthly_installment": float(decision["monthly_installment"]) if decision["monthly_installment"] else None,
                    "reason": decision["reason"],
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "customer_id": customer.customer_id,
                "approval": True,
                "interest_rate": float(decision["requested_rate"]),
                "corrected_interest_rate": float(decision["corrected_rate"]),
                "tenure": decision["tenure"],
                "monthly_installment": float(decision["monthly_installment"]),
            },
            status=status.HTTP_200_OK,
        )


def evaluate_loan_request(customer: Customer, loans, loan_amount: Decimal, requested_rate: Decimal, tenure: int):
    score, active_amount, active_emi = _compute_credit_score(customer, loans)

    if score == 0:
        return {
            "approval": False,
            "requested_rate": requested_rate,
            "corrected_rate": None,
            "tenure": tenure,
            "monthly_installment": None,
            "reason": "credit score zero (active loans exceed limit)",
        }

    if score > 50:
        min_rate = requested_rate
    elif score > 30:
        min_rate = Decimal("12")
    elif score > 10:
        min_rate = Decimal("16")
    else:
        return {
            "approval": False,
            "requested_rate": requested_rate,
            "corrected_rate": None,
            "tenure": tenure,
            "monthly_installment": None,
            "reason": "credit score too low",
        }

    corrected_rate = max(requested_rate, min_rate)
    monthly_installment = _compute_emi(loan_amount, corrected_rate, tenure)

    total_emi = active_emi + monthly_installment
    if total_emi > (customer.monthly_salary * Decimal("0.5")):
        return {
            "approval": False,
            "requested_rate": requested_rate,
            "corrected_rate": corrected_rate,
            "tenure": tenure,
            "monthly_installment": monthly_installment,
            "reason": "emi exceeds 50% of monthly salary",
        }

    return {
        "approval": True,
        "requested_rate": requested_rate,
        "corrected_rate": corrected_rate,
        "tenure": tenure,
        "monthly_installment": monthly_installment,
    }


class CreateLoanView(APIView):
    def post(self, request):
        serializer = CreateLoanRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            customer = Customer.objects.get(customer_id=data["customer_id"])
        except Customer.DoesNotExist:
            return Response({"detail": "Customer not found."}, status=status.HTTP_404_NOT_FOUND)

        loans = Loan.objects.filter(customer=customer)
        decision = evaluate_loan_request(
            customer=customer,
            loans=loans,
            loan_amount=Decimal(data["loan_amount"]),
            requested_rate=Decimal(data["interest_rate"]),
            tenure=int(data["tenure"]),
        )

        if not decision["approval"]:
            return Response(
                {
                    "loan_id": None,
                    "customer_id": customer.customer_id,
                    "loan_approved": False,
                    "message": decision["reason"],
                    "monthly_installment": float(decision["monthly_installment"]) if decision["monthly_installment"] else None,
                },
                status=status.HTTP_200_OK,
            )

        with transaction.atomic():
            next_id = (Loan.objects.order_by("-loan_id").first().loan_id + 1) if Loan.objects.exists() else 1
            start_date = timezone.now().date()
            end_date = start_date + timedelta(days=decision["tenure"] * 30)

            loan = Loan.objects.create(
                loan_id=next_id,
                customer=customer,
                loan_amount=Decimal(data["loan_amount"]),
                tenure=decision["tenure"],
                interest_rate=decision["corrected_rate"],
                monthly_repayment=decision["monthly_installment"],
                emis_paid_on_time=0,
                start_date=start_date,
                end_date=end_date,
            )
            customer.current_debt = customer.current_debt + Decimal(data["loan_amount"])
            customer.save(update_fields=["current_debt"])

        return Response(
            {
                "loan_id": loan.loan_id,
                "customer_id": customer.customer_id,
                "loan_approved": True,
                "message": "Loan approved",
                "monthly_installment": float(decision["monthly_installment"]),
            },
            status=status.HTTP_200_OK,
        )


class ViewLoanView(APIView):
    def get(self, request, loan_id: int):
        try:
            loan = Loan.objects.select_related("customer").get(loan_id=loan_id)
        except Loan.DoesNotExist:
            return Response({"detail": "Loan not found."}, status=status.HTTP_404_NOT_FOUND)

        customer = loan.customer
        return Response(
            {
                "loan_id": loan.loan_id,
                "customer": {
                    "id": customer.customer_id,
                    "first_name": customer.first_name,
                    "last_name": customer.last_name,
                    "phone_number": customer.phone_number,
                    "age": customer.age,
                },
                "loan_amount": float(loan.loan_amount),
                "interest_rate": float(loan.interest_rate),
                "monthly_installment": float(loan.monthly_repayment),
                "tenure": loan.tenure,
            },
            status=status.HTTP_200_OK,
        )


class ViewLoansByCustomerView(APIView):
    def get(self, request, customer_id: int):
        loans = Loan.objects.filter(customer__customer_id=customer_id)
        if not loans.exists():
            return Response({"detail": "No loans found for this customer."}, status=status.HTTP_404_NOT_FOUND)

        today = timezone.now().date()
        items = []
        for loan in loans:
            # approximate repayments left based on time remaining vs tenure
            total_days = max((loan.end_date - loan.start_date).days, 1)
            elapsed_days = max((today - loan.start_date).days, 0)
            elapsed_ratio = min(1, elapsed_days / total_days)
            paid_emis = int(round(elapsed_ratio * loan.tenure))
            repayments_left = max(0, loan.tenure - paid_emis)

            items.append(
                {
                    "loan_id": loan.loan_id,
                    "loan_amount": float(loan.loan_amount),
                    "interest_rate": float(loan.interest_rate),
                    "monthly_installment": float(loan.monthly_repayment),
                    "repayments_left": repayments_left,
                }
            )

        return Response(items, status=status.HTTP_200_OK)
    
from rest_framework import viewsets
from .models import Customer
from .serializers import CustomerSerializer

class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
