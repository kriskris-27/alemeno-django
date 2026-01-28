from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .models import Customer, Loan


class CreditApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def register_customer(self, **kwargs):
        payload = {
            "first_name": "Jane",
            "last_name": "Doe",
            "age": 30,
            "monthly_income": 75000,
            "phone_number": "1234567890",
        }
        payload.update(kwargs)
        resp = self.client.post(reverse("register"), payload, format="json")
        self.assertEqual(resp.status_code, 201)
        return resp.json()

    def test_register_creates_customer_and_limit(self):
        data = self.register_customer()
        self.assertEqual(data["customer_id"], 1)
        self.assertEqual(data["approved_limit"], 2700000)  # 36 * 75000 rounded to lakh
        customer = Customer.objects.get(customer_id=data["customer_id"])
        self.assertEqual(customer.approved_limit, Decimal("2700000"))

    def test_check_eligibility_approves(self):
        reg = self.register_customer()
        payload = {
            "customer_id": reg["customer_id"],
            "loan_amount": 300000,
            "interest_rate": 12,
            "tenure": 24,
        }
        resp = self.client.post(reverse("check-eligibility"), payload, format="json")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["approval"])
        self.assertAlmostEqual(body["corrected_interest_rate"], 12.0)
        self.assertGreater(body["monthly_installment"], 0)

    def test_create_loan_and_view(self):
        reg = self.register_customer()
        create_payload = {
            "customer_id": reg["customer_id"],
            "loan_amount": 300000,
            "interest_rate": 12,
            "tenure": 12,
        }
        create_resp = self.client.post(reverse("create-loan"), create_payload, format="json")
        self.assertEqual(create_resp.status_code, 200)
        create_body = create_resp.json()
        self.assertTrue(create_body["loan_approved"])
        loan_id = create_body["loan_id"]

        # view single loan
        view_resp = self.client.get(reverse("view-loan", args=[loan_id]))
        self.assertEqual(view_resp.status_code, 200)
        view_body = view_resp.json()
        self.assertEqual(view_body["loan_id"], loan_id)
        self.assertEqual(view_body["customer"]["id"], reg["customer_id"])

        # view loans by customer
        list_resp = self.client.get(reverse("view-loans", args=[reg["customer_id"]]))
        self.assertEqual(list_resp.status_code, 200)
        list_body = list_resp.json()
        self.assertEqual(len(list_body), 1)
        self.assertEqual(list_body[0]["loan_id"], loan_id)
