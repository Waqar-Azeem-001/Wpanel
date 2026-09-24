from django import forms


class LoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email", "autofocus": True}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))


class RegisterForm(forms.Form):
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password_confirm = forms.CharField(label="Confirm password", widget=forms.PasswordInput(
        attrs={"autocomplete": "new-password"}))

    def clean(self):
        data = super().clean()
        if data.get("password") and data.get("password") != data.get("password_confirm"):
            self.add_error("password_confirm", "Passwords do not match.")
        return data


class ProfileForm(forms.Form):
    # A plain Form (not ModelForm) so validation never mutates the user; the service applies changes.
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    phone = forms.CharField(max_length=32, required=False)


class PasswordResetRequestForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"autocomplete": "email"}))


class SetPasswordForm(forms.Form):
    new_password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    new_password_confirm = forms.CharField(label="Confirm new password", widget=forms.PasswordInput(
        attrs={"autocomplete": "new-password"}))

    def clean(self):
        data = super().clean()
        if data.get("new_password") and data.get("new_password") != data.get("new_password_confirm"):
            self.add_error("new_password_confirm", "Passwords do not match.")
        return data


class PasswordChangeForm(SetPasswordForm):
    old_password = forms.CharField(label="Current password", widget=forms.PasswordInput(
        attrs={"autocomplete": "current-password"}))
    field_order = ["old_password", "new_password", "new_password_confirm"]
