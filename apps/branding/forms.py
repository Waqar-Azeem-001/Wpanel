from django import forms

from .models import BrandSettings


class BrandForm(forms.ModelForm):
    logo = forms.FileField(required=False, label="Logo",
                           help_text="PNG, JPEG, GIF or WebP, up to 512 KB. Shown in the navigation bar and emails.",
                           widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/gif,image/webp"}))
    remove_logo = forms.BooleanField(required=False, label="Remove the current logo")
    favicon = forms.FileField(required=False, label="Favicon",
                              help_text="PNG, ICO, JPEG, GIF or WebP, up to 128 KB (square works best).",
                              widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/x-icon,image/jpeg,image/gif,image/webp"}))
    remove_favicon = forms.BooleanField(required=False, label="Remove the current favicon")
    logo_dark = forms.FileField(required=False, label="Logo for dark backgrounds",
                                help_text="A light (white) version of the logo, up to 512 KB. Used in the dark theme and on the dark footer.",
                                widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/jpeg,image/gif,image/webp"}))
    remove_logo_dark = forms.BooleanField(required=False, label="Remove the current logo for dark backgrounds")
    mark_dark = forms.FileField(required=False, label="Icon for dark backgrounds",
                                help_text="A light (white) version of the small icon, up to 128 KB. Used in the header in the dark theme.",
                                widget=forms.ClearableFileInput(attrs={"accept": "image/png,image/x-icon,image/jpeg,image/gif,image/webp"}))
    remove_mark_dark = forms.BooleanField(required=False, label="Remove the current icon for dark backgrounds")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("theme", "custom_look"):  # a post that does not mention them keeps what is saved
            self.fields[name].required = False

    def clean_theme(self):
        return self.cleaned_data.get("theme") or self.instance.theme or "custom"

    def clean_custom_look(self):
        return self.cleaned_data.get("custom_look") or self.instance.custom_look or "signature"

    class Meta:
        model = BrandSettings
        fields = ["site_name", "primary_color", "accent_color", "support_email", "footer_text", "date_format",
                  "money_format", "theme", "custom_look"]
        widgets = {"primary_color": forms.TextInput(attrs={"type": "color"}),
                   "accent_color": forms.TextInput(attrs={"type": "color"})}
        labels = {"theme": "Look", "custom_look": "Type, corners and header", "site_name": "Company / site name", "primary_color": "Primary colour", "accent_color": "Accent colour",
                  "date_format": "How dates are written", "money_format": "How money is written"}
