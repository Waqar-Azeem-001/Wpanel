"""/api/v1/ routes. Included under the ``v1`` namespace (DRF NamespaceVersioning)."""
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

from apps.accounts import api as accounts_api
from apps.audit.api import AuditEventViewSet
from apps.billing.api import CouponViewSet, PaymentMethodViewSet, TaxRuleViewSet
from apps.clients.api import ClientViewSet, MyClientViewSet
from apps.core.views import HealthView
from apps.domains.api import AvailabilityView, DomainViewSet, TldPricingViewSet
from apps.hosting.api import HostingAccountViewSet
from apps.notifications.api import NotificationViewSet
from apps.orders import api as orders_api
from apps.products.api import AddonViewSet, ProductViewSet, ServerViewSet

router = DefaultRouter()
router.register("users", accounts_api.UserAdminViewSet, basename="user")
router.register("notifications", NotificationViewSet, basename="notification")
router.register("audit-events", AuditEventViewSet, basename="audit-event")
router.register("clients", ClientViewSet, basename="client")
router.register("me/clients", MyClientViewSet, basename="my-client")
router.register("products", ProductViewSet, basename="product")
router.register("addons", AddonViewSet, basename="addon")
router.register("servers", ServerViewSet, basename="server")
router.register("domains", DomainViewSet, basename="domain")
router.register("tld-pricing", TldPricingViewSet, basename="tld-pricing")
router.register("hosting-accounts", HostingAccountViewSet, basename="hosting-account")
router.register("payment-methods", PaymentMethodViewSet, basename="payment-method")
router.register("tax-rules", TaxRuleViewSet, basename="tax-rule")
router.register("coupons", CouponViewSet, basename="coupon")
router.register("orders", orders_api.OrderViewSet, basename="order")

auth_patterns = [
    path("register/", accounts_api.RegisterView.as_view(), name="register"),
    path("login/", accounts_api.LoginView.as_view(), name="login"),
    path("refresh/", accounts_api.RefreshView.as_view(), name="refresh"),
    path("logout/", accounts_api.LogoutView.as_view(), name="logout"),
    path("verify-email/", accounts_api.VerifyEmailView.as_view(), name="verify-email"),
    path("resend-verification/", accounts_api.ResendVerificationView.as_view(), name="resend-verification"),
    path("password-reset/", accounts_api.PasswordResetView.as_view(), name="password-reset"),
    path("password-reset/confirm/", accounts_api.PasswordResetConfirmView.as_view(), name="password-reset-confirm"),
    path("password-change/", accounts_api.PasswordChangeView.as_view(), name="password-change"),
]

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("auth/", include(auth_patterns)),
    path("me/", accounts_api.MeView.as_view(), name="me"),
    path("domains/availability/", AvailabilityView.as_view(), name="domain-availability"),
    path("cart/", orders_api.CartView.as_view(), name="cart"),
    path("cart/items/", orders_api.CartItemsView.as_view(), name="cart-items"),
    path("cart/items/<int:pk>/", orders_api.CartItemDetailView.as_view(), name="cart-item"),
    path("cart/coupon/", orders_api.CartCouponView.as_view(), name="cart-coupon"),
    path("cart/checkout/", orders_api.CheckoutView.as_view(), name="cart-checkout"),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="v1:schema"), name="docs"),
    path("", include(router.urls)),
]
