from django.urls import path
from .views import RegisterView, CheckEligibilityView

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("check-eligibility/", CheckEligibilityView.as_view(), name="check-eligibility"),
]
