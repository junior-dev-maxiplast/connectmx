from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError


User = get_user_model()


class ForgotPasswordForm(forms.Form):
    user_id = forms.CharField(label="Matricula", max_length=150)
    email = forms.EmailField(label="E-mail", max_length=254)
    new_password1 = forms.CharField(
        label="Nova senha",
        strip=False,
        widget=forms.PasswordInput(),
    )
    new_password2 = forms.CharField(
        label="Confirmar nova senha",
        strip=False,
        widget=forms.PasswordInput(),
    )

    error_messages = {
        "user_not_found": "Nao encontramos um usuario com a matricula e o e-mail informados.",
        "password_mismatch": "As senhas informadas nao coincidem.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        placeholders = {
            "user_id": "Informe sua matricula",
            "email": "Informe seu e-mail cadastrado",
            "new_password1": "Digite a nova senha",
            "new_password2": "Confirme a nova senha",
        }
        for field_name, field in self.fields.items():
            field.widget.attrs.update(
                {
                    "class": "login-input",
                    "placeholder": placeholders.get(field_name, ""),
                    "autocomplete": "off",
                }
            )

        self.fields["new_password1"].widget.attrs["autocomplete"] = "new-password"
        self.fields["new_password2"].widget.attrs["autocomplete"] = "new-password"

    def clean_user_id(self):
        return (self.cleaned_data.get("user_id") or "").strip()

    def clean_email(self):
        return (self.cleaned_data.get("email") or "").strip().lower()

    def clean(self):
        cleaned_data = super().clean()
        user_id = cleaned_data.get("user_id")
        email = cleaned_data.get("email")
        password1 = cleaned_data.get("new_password1")
        password2 = cleaned_data.get("new_password2")

        user = None
        if user_id and email:
            user = User.objects.filter(userId__iexact=user_id, email__iexact=email).first()
            if not user:
                raise forms.ValidationError(self.error_messages["user_not_found"])

        if password1 and password2 and password1 != password2:
            self.add_error("new_password2", self.error_messages["password_mismatch"])

        if user and password1:
            try:
                validate_password(password1, user=user)
            except ValidationError as exc:
                self.add_error("new_password1", exc)

        cleaned_data["user"] = user
        return cleaned_data

    def save(self):
        user = self.cleaned_data["user"]
        user.set_password(self.cleaned_data["new_password1"])
        user.save(update_fields=["password"])
        return user
