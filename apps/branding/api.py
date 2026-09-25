"""
REST API for the brand: the tokens a web or mobile client needs to look like the business (public), and the staff
change (``manage_settings``). Images are uploaded as ``multipart/form-data`` (fields ``logo`` and ``favicon``).
"""
from django.urls import reverse
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.roles import perm
from apps.core.exceptions import ServiceError

from . import services
from .models import DATE_FORMATS, MONEY_FORMATS, validate_hex


class BrandSerializer(serializers.Serializer):
    name = serializers.CharField()
    primary_color = serializers.CharField()
    primary_dark_color = serializers.CharField()
    accent_color = serializers.CharField()
    support_email = serializers.CharField(allow_blank=True)
    footer_text = serializers.CharField(allow_blank=True)
    date_format = serializers.CharField()
    money_format = serializers.CharField()
    logo_url = serializers.CharField(allow_null=True)
    favicon_url = serializers.CharField(allow_null=True)
    version = serializers.IntegerField()


class BrandUpdateSerializer(serializers.Serializer):
    site_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    primary_color = serializers.CharField(max_length=7, required=False, validators=[validate_hex])
    accent_color = serializers.CharField(max_length=7, required=False, validators=[validate_hex])
    support_email = serializers.EmailField(required=False, allow_blank=True)
    footer_text = serializers.CharField(max_length=300, required=False, allow_blank=True)
    date_format = serializers.ChoiceField(choices=[c for c, _ in DATE_FORMATS], required=False)
    money_format = serializers.ChoiceField(choices=[c for c, _ in MONEY_FORMATS], required=False)
    logo = serializers.FileField(required=False)
    favicon = serializers.FileField(required=False)
    remove_logo = serializers.BooleanField(required=False)
    remove_favicon = serializers.BooleanField(required=False)


def _payload():
    b = services.get()
    version = f"?v={b.version}"
    return {"name": b.name, "primary_color": b.primary, "primary_dark_color": b.primary_dark,
            "accent_color": b.accent, "support_email": b.support_email, "footer_text": b.footer_text,
            "date_format": b.date_format, "money_format": b.money_format,
            "logo_url": reverse("brand_logo") + version if b.has_logo else None,
            "favicon_url": reverse("brand_favicon") + version if b.has_favicon else None, "version": b.version}


class BrandView(APIView):
    """GET is public (a mobile app needs the brand before anyone signs in); PATCH needs ``manage_settings``."""

    permission_classes = [AllowAny]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @extend_schema(responses=BrandSerializer)
    def get(self, request):
        return Response(BrandSerializer(_payload()).data)

    @extend_schema(request=BrandUpdateSerializer, responses=BrandSerializer)
    def patch(self, request):
        if not (request.user.is_authenticated and request.user.has_perm(perm("manage_settings"))):
            raise ServiceError("You do not have permission to perform this action.", code="permission_denied",
                               status_code=403)
        serializer = BrandUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        logo, favicon = data.pop("logo", None), data.pop("favicon", None)
        services.save_settings(
            request.user, values=data, request=request, logo=logo.read() if logo else None,
            favicon=favicon.read() if favicon else None, remove_logo=data.pop("remove_logo", False),
            remove_favicon=data.pop("remove_favicon", False))
        return Response(BrandSerializer(_payload()).data)
