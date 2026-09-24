from django.shortcuts import get_object_or_404
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response

from apps.accounts.roles import perm
from apps.core.permissions import HasPortalPermission, PublicReadPermission

from . import services
from .models import Addon, BillingCycle, CatalogStatus, Product, Server, ServerStatus


class PriceSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    billing_cycle = serializers.ChoiceField(choices=BillingCycle.choices)
    custom_months = serializers.IntegerField(min_value=0, required=False, default=0)
    price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0)
    setup_fee = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=0, required=False, default=0)
    is_active = serializers.BooleanField(read_only=True)
    months = serializers.IntegerField(read_only=True)


class SetPriceActiveSerializer(serializers.Serializer):
    is_active = serializers.BooleanField()


class ProductSerializer(serializers.ModelSerializer):
    prices = PriceSerializer(many=True, read_only=True)
    server_ids = serializers.PrimaryKeyRelatedField(source="servers", queryset=Server.objects.all(), many=True,
                                                     required=False)

    class Meta:
        model = Product
        fields = ["id", "name", "slug", "type", "description", "status", "resource_limits", "server_ids",
                  "whm_package_name", "auto_setup", "default_auto_renew", "prices", "created_at", "updated_at"]
        read_only_fields = ["id", "slug", "status", "prices", "created_at", "updated_at"]

    def get_fields(self):
        fields = super().get_fields()
        fields["servers"] = serializers.SlugRelatedField(slug_field="name", many=True, read_only=True)
        return fields


class PublicProductSerializer(serializers.ModelSerializer):
    """What a prospective customer sees: no internal fields, only active prices."""

    prices = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ["id", "name", "slug", "type", "description", "resource_limits", "prices"]
        read_only_fields = fields

    def get_prices(self, product) -> list:
        return PriceSerializer(product.prices.filter(is_active=True), many=True).data


class SetProductStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=CatalogStatus.choices)


class ProductFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")

    class Meta:
        model = Product
        fields = ["type", "status"]

    def filter_search(self, queryset, name, value):
        from .services import search_catalog

        return search_catalog(queryset, value)


class ProductViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                     mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """
    Public catalog + staff management in one endpoint: anyone may browse active
    products (GET); ``manage_products`` is required to create or change one.
    """

    permission_classes = [PublicReadPermission]
    required_permissions = {"*": [perm("manage_products")]}
    filterset_class = ProductFilter
    filter_backends = [filters.DjangoFilterBackend, OrderingFilter]
    ordering_fields = ["name", "created_at"]
    lookup_field = "slug"

    def get_queryset(self):
        return services.visible_products_for_user(self.request.user)

    def get_serializer_class(self):
        if self.request.user.is_authenticated and self.request.user.has_perm(perm("view_products")):
            return ProductSerializer
        return PublicProductSerializer

    def create(self, request, *args, **kwargs):
        serializer = ProductSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        server_ids = [s.pk for s in data.pop("servers", [])]
        product = services.create_product(request.user, data, request=request)
        if server_ids:
            services.set_product_servers(request.user, product, server_ids, request=request)
        return Response(ProductSerializer(product).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        data = dict(serializer.validated_data)
        server_ids = data.pop("servers", None)
        services.update_product(self.request.user, serializer.instance, data, request=self.request)
        if server_ids is not None:
            services.set_product_servers(self.request.user, serializer.instance, [s.pk for s in server_ids],
                                         request=self.request)

    @extend_schema(request=SetProductStatusSerializer, responses=ProductSerializer)
    @action(detail=True, methods=["post"], url_path="status")
    def set_status(self, request, *args, **kwargs):
        serializer = SetProductStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = services.set_product_status(request.user, self.get_object(), serializer.validated_data["status"],
                                              request=request)
        return Response(ProductSerializer(product).data)

    def _price_view(self, request, item, price_id=None):
        if request.method == "GET":
            prices = item.prices.all() if request.user.has_perm(perm("view_products")) else \
                item.prices.filter(is_active=True)
            return Response(PriceSerializer(prices, many=True).data)
        if price_id is None and request.method == "POST":
            serializer = PriceSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            entry = services.set_price(request.user, item, request=request, **serializer.validated_data)
            return Response(PriceSerializer(entry).data, status=status.HTTP_201_CREATED)
        model, fk = services._price_model_and_fk(item)
        entry = get_object_or_404(model, pk=price_id, **{fk: item})
        if request.method == "PATCH":
            serializer = SetPriceActiveSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            entry = services.set_price_active(request.user, item, entry, serializer.validated_data["is_active"],
                                              request=request)
            return Response(PriceSerializer(entry).data)
        services.remove_price(request.user, item, entry, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=PriceSerializer, responses={200: PriceSerializer(many=True), 201: PriceSerializer})
    @action(detail=True, methods=["get", "post"], url_path="prices")
    def prices(self, request, *args, **kwargs):
        return self._price_view(request, self.get_object())

    @extend_schema(request=SetPriceActiveSerializer, responses=PriceSerializer)
    @action(detail=True, methods=["patch", "delete"], url_path=r"prices/(?P<price_id>\d+)")
    def price_detail(self, request, price_id=None, *args, **kwargs):
        return self._price_view(request, self.get_object(), price_id=price_id)


class AddonSerializer(serializers.ModelSerializer):
    prices = PriceSerializer(many=True, read_only=True)

    class Meta:
        model = Addon
        fields = ["id", "name", "slug", "description", "status", "prices", "created_at", "updated_at"]
        read_only_fields = ["id", "slug", "status", "prices", "created_at", "updated_at"]


class PublicAddonSerializer(serializers.ModelSerializer):
    prices = serializers.SerializerMethodField()

    class Meta:
        model = Addon
        fields = ["id", "name", "slug", "description", "prices"]
        read_only_fields = fields

    def get_prices(self, addon) -> list:
        return PriceSerializer(addon.prices.filter(is_active=True), many=True).data


class AddonFilter(filters.FilterSet):
    search = filters.CharFilter(method="filter_search")

    class Meta:
        model = Addon
        fields = ["status"]

    def filter_search(self, queryset, name, value):
        from .services import search_catalog

        return search_catalog(queryset, value)


class AddonViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                   mixins.UpdateModelMixin, viewsets.GenericViewSet):
    permission_classes = [PublicReadPermission]
    required_permissions = {"*": [perm("manage_products")]}
    filterset_class = AddonFilter
    lookup_field = "slug"

    def get_queryset(self):
        return services.visible_addons_for_user(self.request.user)

    def get_serializer_class(self):
        if self.request.user.is_authenticated and self.request.user.has_perm(perm("view_products")):
            return AddonSerializer
        return PublicAddonSerializer

    def create(self, request, *args, **kwargs):
        serializer = AddonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        addon = services.create_addon(request.user, serializer.validated_data, request=request)
        return Response(AddonSerializer(addon).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        services.update_addon(self.request.user, serializer.instance, serializer.validated_data, request=self.request)

    @extend_schema(request=SetProductStatusSerializer, responses=AddonSerializer)
    @action(detail=True, methods=["post"], url_path="status")
    def set_status(self, request, *args, **kwargs):
        serializer = SetProductStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        addon = services.set_addon_status(request.user, self.get_object(), serializer.validated_data["status"],
                                          request=request)
        return Response(AddonSerializer(addon).data)

    def _price_view(self, request, item, price_id=None):
        if request.method == "GET":
            prices = item.prices.all() if request.user.has_perm(perm("view_products")) else \
                item.prices.filter(is_active=True)
            return Response(PriceSerializer(prices, many=True).data)
        if price_id is None and request.method == "POST":
            serializer = PriceSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            entry = services.set_price(request.user, item, request=request, **serializer.validated_data)
            return Response(PriceSerializer(entry).data, status=status.HTTP_201_CREATED)
        model, fk = services._price_model_and_fk(item)
        entry = get_object_or_404(model, pk=price_id, **{fk: item})
        if request.method == "PATCH":
            serializer = SetPriceActiveSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            entry = services.set_price_active(request.user, item, entry, serializer.validated_data["is_active"],
                                              request=request)
            return Response(PriceSerializer(entry).data)
        services.remove_price(request.user, item, entry, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=PriceSerializer, responses={200: PriceSerializer(many=True), 201: PriceSerializer})
    @action(detail=True, methods=["get", "post"], url_path="prices")
    def prices(self, request, *args, **kwargs):
        return self._price_view(request, self.get_object())

    @extend_schema(request=SetPriceActiveSerializer, responses=PriceSerializer)
    @action(detail=True, methods=["patch", "delete"], url_path=r"prices/(?P<price_id>\d+)")
    def price_detail(self, request, price_id=None, *args, **kwargs):
        return self._price_view(request, self.get_object(), price_id=price_id)


class ServerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Server
        fields = ["id", "name", "hostname", "ip_address", "status", "max_accounts", "notes", "created_at",
                  "updated_at"]
        read_only_fields = ["id", "status", "created_at", "updated_at"]


class SetServerStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=ServerStatus.choices)


class ServerViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                    mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """Staff-only: no public visibility of infrastructure."""

    queryset = Server.objects.all()
    serializer_class = ServerSerializer
    permission_classes = [HasPortalPermission]  # staff-only, including GET: no public infrastructure listing

    @property
    def required_permissions(self):
        if self.request.method in ("GET", "HEAD", "OPTIONS"):
            return [perm("view_hosting")]
        return [perm("manage_hosting")]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Server.objects.none()
        return Server.objects.all()

    def create(self, request, *args, **kwargs):
        serializer = ServerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        server = services.create_server(request.user, serializer.validated_data, request=request)
        return Response(ServerSerializer(server).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        services.update_server(self.request.user, serializer.instance, serializer.validated_data, request=self.request)

    @extend_schema(request=SetServerStatusSerializer, responses=ServerSerializer)
    @action(detail=True, methods=["post"], url_path="status")
    def set_status(self, request, *args, **kwargs):
        serializer = SetServerStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        server = services.set_server_status(request.user, self.get_object(), serializer.validated_data["status"],
                                            request=request)
        return Response(ServerSerializer(server).data)
