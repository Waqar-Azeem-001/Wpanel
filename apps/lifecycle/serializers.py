"""A read-only ``lifecycle_stage`` field for the hosting and domain serializers."""
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import LifecycleSettings, Stage


class LifecycleStageMixin(serializers.Serializer):
    lifecycle_stage = serializers.SerializerMethodField(
        help_text="Where the service is in its life (derived from its paid-through date); null for a service "
                  "that is not live.")

    @extend_schema_field(serializers.ChoiceField(choices=Stage.choices, allow_null=True))
    def get_lifecycle_stage(self, service):
        from apps.billing.models import BillingSettings

        from . import services

        if not hasattr(self, "_stage_settings"):  # one lookup per response, not one per row
            self._stage_settings = (LifecycleSettings.load(), BillingSettings.load().renewal_invoice_days)
        config, lead = self._stage_settings
        stage = services.stage_of(service, config=config, lead_days=lead)
        return stage.value if stage else None
