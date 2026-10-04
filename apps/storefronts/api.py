"""
The storefront-facing catalog API (external/WordPress integration): read-only, credentialed with a storefront's own
key (never a staff/customer session), and scoped to exactly what that storefront was published. No pricing engine
of its own - every figure here is read straight from the same ``ProductPrice``/``AddonPrice``/``TldPricing`` rows
the rest of the project already treats as authoritative (Rule 3).
"""
from django.conf import settings
from django.db.models import Q
from django.http import Http404
from django.urls import reverse
from rest_framework import generics, mixins, serializers, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.exceptions import ServiceError
from apps.domains import services as domain_services
from apps.domains.models import TldPricing
from apps.products.models import Addon, CatalogStatus, StoreCategory

from . import services
from .authentication import StorefrontKeyAuthentication
from .permissions import IsStorefrontAuthenticated
from .throttling import StorefrontRateThrottle


class _StorefrontView:
    """Shared plumbing for every storefront-facing endpoint: its own credential, its own throttle scope, never any
    of the JWT/session/staff-permission machinery the rest of the API uses."""

    authentication_classes = [StorefrontKeyAuthentication]
    permission_classes = [IsStorefrontAuthenticated]
    throttle_classes = [StorefrontRateThrottle]
    throttle_scope = "storefront"
    pagination_class = None  # a storefront caches the whole catalog client-side (Section 10 of the spec); no paging to track


def _absolute(path):
    return settings.SITE_URL.rstrip("/") + path


class StorefrontCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = StoreCategory
        fields = ["slug", "name", "group"]
        read_only_fields = fields


class StorefrontPriceSerializer(serializers.Serializer):
    billing_cycle = serializers.CharField()
    months = serializers.IntegerField(allow_null=True)
    amount = serializers.DecimalField(source="price", max_digits=10, decimal_places=2)
    setup_fee = serializers.DecimalField(max_digits=10, decimal_places=2)
    currency = serializers.SerializerMethodField()

    def get_currency(self, obj) -> str:
        return settings.STORE_CURRENCY


class StorefrontProductSerializer(serializers.Serializer):
    """One plan, as published to the storefront in ``context["storefront"]`` - never the authoritative record
    itself, so a display override on one storefront can never leak into another's, or into the product itself."""

    id = serializers.IntegerField()
    slug = serializers.CharField()
    type = serializers.CharField()
    name = serializers.SerializerMethodField()
    description = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()
    pricing = serializers.SerializerMethodField()
    features = serializers.SerializerMethodField()
    featured = serializers.SerializerMethodField()
    sort_order = serializers.SerializerMethodField()
    order_url = serializers.SerializerMethodField()
    updated_at = serializers.DateTimeField()

    def _link(self, product):
        storefront = self.context["storefront"]
        for link in product.storefront_links.all():  # prefetched by services.products_for - no extra query per row
            if link.storefront_id == storefront.pk:
                return link
        return None  # should not happen (the queryset is already filtered to this storefront's own links)

    def get_name(self, product) -> str:
        link = self._link(product)
        return link.display_title if link else product.name

    def get_description(self, product) -> str:
        link = self._link(product)
        return link.display_description if link else product.description

    def get_featured(self, product) -> bool:
        link = self._link(product)
        return bool(link and link.featured)

    def get_sort_order(self, product) -> int:
        link = self._link(product)
        return link.sort_order if link else 100

    def get_category(self, product) -> dict | None:
        if not product.category_id:
            return None
        return StorefrontCategorySerializer(product.category).data

    def get_pricing(self, product) -> dict:
        return {price.billing_cycle if price.billing_cycle != "custom" else f"custom_{price.custom_months}":
                StorefrontPriceSerializer(price).data
                for price in product.prices.all() if price.is_active}

    def get_features(self, product) -> list:
        return [f"{title}: {value}" for title, value in product.limit_items]

    def get_order_url(self, product) -> str:
        return _absolute(reverse("catalog:product_detail", args=[product.slug]))


class StorefrontProductViewSet(_StorefrontView, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                               viewsets.GenericViewSet):
    serializer_class = StorefrontProductSerializer
    lookup_field = "slug"
    lookup_value_regex = r"[^/]+"  # a slug normally, but get_object() below also accepts a plain numeric id

    def get_queryset(self):
        return services.products_for(self.request.auth)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["storefront"] = self.request.auth
        return context

    def get_object(self):
        """By slug (the normal case) or by numeric id, so a plugin can link with whichever it already has."""
        lookup = self.kwargs["slug"]
        query = Q(slug=lookup)
        if lookup.isdigit():
            query |= Q(pk=int(lookup))
        obj = self.filter_queryset(self.get_queryset()).filter(query).first()
        if obj is None:
            raise Http404
        self.check_object_permissions(self.request, obj)
        return obj


class StorefrontCategoryListView(_StorefrontView, generics.ListAPIView):
    """Only categories that actually hold a plan published to this storefront - an empty category is noise a
    storefront's own menu has no use for."""

    serializer_class = StorefrontCategorySerializer

    def get_queryset(self):
        category_ids = services.products_for(self.request.auth).values_list("category_id", flat=True)
        return StoreCategory.objects.filter(pk__in=category_ids, is_active=True).order_by("sort_order", "name")


class StorefrontAddonSerializer(serializers.Serializer):
    """
    Add-ons are not currently restricted to particular plans anywhere in the project (a documented, pre-existing
    limitation - see the deferred-items list: "any add-on attaches to any product for now"), so this deliberately
    does not invent a compatibility rule that does not exist yet: every active add-on is offered to every storefront
    that has at least one published plan, exactly as the general public catalog already does.
    """

    id = serializers.IntegerField()
    slug = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    pricing = serializers.SerializerMethodField()

    def get_pricing(self, addon) -> dict:
        return {price.billing_cycle if price.billing_cycle != "custom" else f"custom_{price.custom_months}":
                StorefrontPriceSerializer(price).data
                for price in addon.prices.all() if price.is_active}


class StorefrontAddonListView(_StorefrontView, generics.ListAPIView):
    serializer_class = StorefrontAddonSerializer

    def get_queryset(self):
        return Addon.objects.filter(status=CatalogStatus.ACTIVE).prefetch_related("prices")


class StorefrontTldPricingSerializer(serializers.ModelSerializer):
    currency = serializers.SerializerMethodField()

    class Meta:
        model = TldPricing
        fields = ["tld", "register_price", "renew_price", "transfer_price", "min_years", "max_years", "currency"]
        read_only_fields = fields

    def get_currency(self, obj) -> str:
        return settings.STORE_CURRENCY


class StorefrontTldPricingListView(_StorefrontView, generics.ListAPIView):
    """Domain pricing is not per-storefront either (Step 6 of the spec does not ask for it) - every active TLD, the
    same set the public domain search already uses."""

    serializer_class = StorefrontTldPricingSerializer

    def get_queryset(self):
        return TldPricing.objects.filter(is_active=True).order_by("tld")


class StorefrontDomainSearchView(_StorefrontView, APIView):
    """
    Is this domain free to register, and at what price? Answered by the same availability check and the same
    ``TldPricing`` row the portal's own public search uses (Rule 3) - the storefront only displays it, and links the
    customer on to the portal's search page, where registration is actually ordered.

        GET /api/v1/storefront/domains/search/?name=example.com
    """

    def get(self, request):
        name = (request.query_params.get("name") or "").strip().lower()
        if not name:
            return Response({"detail": "Give a domain name, for example ?name=example.com"}, status=400)
        try:
            available, pricing = domain_services.check_availability(name)
        except ServiceError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=getattr(exc, "status_code", 400))
        except Exception as exc:  # noqa: BLE001 - the domain validator raises django's ValidationError
            messages = getattr(exc, "messages", None)
            return Response({"detail": messages[0] if messages else "Not a valid domain name."}, status=400)
        return Response({
            "domain": name,
            "available": bool(available),
            "tld": pricing.tld,
            "register_price": str(pricing.register_price),
            "renew_price": str(pricing.renew_price),
            "currency": settings.STORE_CURRENCY,
            "order_url": _absolute(reverse("domains_public:search")) + f"?domain={name}&years=1",
        })
