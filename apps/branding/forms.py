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

    class Meta:
        model = BrandSettings
        fields = ["site_name", "primary_color", "accent_color", "support_email", "footer_text", "date_format",
                  "money_format"]
        widgets = {"primary_color": forms.TextInput(attrs={"type": "color"}),
                   "accent_color": forms.TextInput(attrs={"type": "color"})}
        labels = {"site_name": "Company / site name", "primary_color": "Primary colour", "accent_color": "Accent colour",
                  "date_format": "How dates are written", "money_format": "How money is written"}
