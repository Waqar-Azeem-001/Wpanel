"""Input validation and output shaping only. Business rules live in ``services``."""
from rest_framework import serializers

from .models import AccountStatus, User
from .roles import Role


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    first_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()


class RefreshTokenSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class TokenSerializer(serializers.Serializer):
    token = serializers.CharField()


class EmailSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)


class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False)


class DetailSerializer(serializers.Serializer):
    detail = serializers.CharField()


class ProfileSerializer(serializers.ModelSerializer):
    email_verified = serializers.BooleanField(source="is_email_verified", read_only=True)
    permissions = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "phone", "role", "status", "email_verified",
                  "is_staff", "date_joined", "last_login", "permissions"]
        read_only_fields = ["id", "email", "role", "status", "email_verified", "is_staff", "date_joined",
                            "last_login", "permissions"]

    def get_permissions(self, user) -> list[str]:
        return sorted(p.split(".", 1)[1] for p in user.get_all_permissions() if p.startswith("accounts."))


class UserAdminSerializer(serializers.ModelSerializer):
    email_verified = serializers.BooleanField(source="is_email_verified", read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "phone", "role", "status", "email_verified",
                  "is_staff", "date_joined", "last_login"]
        read_only_fields = fields


class SetStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=AccountStatus.choices)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)


class SetRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Role.choices)
