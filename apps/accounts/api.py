from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenRefreshView

from apps.core.permissions import HasPortalPermission

from . import serializers as s
from . import services
from .models import User
from .roles import perm


class AuthThrottleMixin:
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class RegisterView(AuthThrottleMixin, APIView):
    permission_classes = [AllowAny]

    @extend_schema(request=s.RegisterSerializer, responses={201: s.ProfileSerializer})
    def post(self, request, *args, **kwargs):
        serializer = s.RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.register_user(request=request, **serializer.validated_data)
        return Response(s.ProfileSerializer(user).data, status=status.HTTP_201_CREATED)


class LoginView(AuthThrottleMixin, APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(request=s.LoginSerializer, responses=s.TokenPairSerializer)
    def post(self, request, *args, **kwargs):
        serializer = s.LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.authenticate_user(request=request, **serializer.validated_data)
        return Response(services.issue_tokens(user, request=request))


class RefreshView(AuthThrottleMixin, TokenRefreshView):
    pass


class LogoutView(APIView):
    @extend_schema(request=s.RefreshTokenSerializer, responses={204: None})
    def post(self, request, *args, **kwargs):
        serializer = s.RefreshTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.revoke_refresh_token(request.user, serializer.validated_data["refresh"], request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class VerifyEmailView(AuthThrottleMixin, APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(request=s.TokenSerializer, responses=s.DetailSerializer)
    def post(self, request, *args, **kwargs):
        serializer = s.TokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.verify_email(serializer.validated_data["token"], request=request)
        return Response({"detail": "Email address verified."})


class ResendVerificationView(AuthThrottleMixin, APIView):
    @extend_schema(request=None, responses=s.DetailSerializer)
    def post(self, request, *args, **kwargs):
        services.send_verification_email(request.user)
        return Response({"detail": "If your email is unverified, a new verification link has been sent."})


class PasswordResetView(AuthThrottleMixin, APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(request=s.EmailSerializer, responses=s.DetailSerializer)
    def post(self, request, *args, **kwargs):
        serializer = s.EmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.request_password_reset(serializer.validated_data["email"], request=request)
        return Response({"detail": "If an account exists for this email, a reset link has been sent."})


class PasswordResetConfirmView(AuthThrottleMixin, APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(request=s.PasswordResetConfirmSerializer, responses=s.DetailSerializer)
    def post(self, request, *args, **kwargs):
        serializer = s.PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        services.reset_password(data["uid"], data["token"], data["new_password"], request=request)
        return Response({"detail": "Password has been reset."})


class PasswordChangeView(AuthThrottleMixin, APIView):
    @extend_schema(request=s.PasswordChangeSerializer, responses=s.DetailSerializer)
    def post(self, request, *args, **kwargs):
        serializer = s.PasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.change_password(request.user, request=request, **serializer.validated_data)
        return Response({"detail": "Password changed. Please sign in again on other devices."})


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = s.ProfileSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return self.request.user

    def perform_update(self, serializer):
        services.update_profile(self.request.user, request=self.request, **serializer.validated_data)


class UserFilter(filters.FilterSet):
    class Meta:
        model = User
        fields = ["role", "status", "is_staff"]


class UserAdminViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Staff user administration: list, inspect, change status, assign role."""

    queryset = User.objects.all()
    serializer_class = s.UserAdminSerializer
    permission_classes = [HasPortalPermission]
    filterset_class = UserFilter
    search_fields = ["email", "first_name", "last_name", "phone"]
    ordering_fields = ["date_joined", "email", "last_login"]

    @property
    def required_permissions(self):
        if self.action == "set_role":
            return [perm("assign_roles")]
        if self.action == "set_status":
            return [perm("manage_users")]
        return [perm("view_users")]

    @extend_schema(request=s.SetStatusSerializer, responses=s.UserAdminSerializer)
    @action(detail=True, methods=["post"], url_path="status")
    def set_status(self, request, *args, **kwargs):
        serializer = s.SetStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.set_account_status(
            request.user, self.get_object(), serializer.validated_data["status"],
            reason=serializer.validated_data.get("reason", ""), request=request,
        )
        return Response(s.UserAdminSerializer(user).data)

    @extend_schema(request=s.SetRoleSerializer, responses=s.UserAdminSerializer)
    @action(detail=True, methods=["post"], url_path="role")
    def set_role(self, request, *args, **kwargs):
        serializer = s.SetRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.assign_role(request.user, self.get_object(), serializer.validated_data["role"],
                                    request=request)
        return Response(s.UserAdminSerializer(user).data)
