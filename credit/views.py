from decimal import Decimal

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Customer
from .serializers import CustomerSerializer, RegisterCustomerSerializer


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
    
from rest_framework import viewsets
from .models import Customer
from .serializers import CustomerSerializer

class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
