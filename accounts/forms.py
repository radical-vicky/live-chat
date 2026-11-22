from django import forms
from allauth.account.forms import SignupForm
from .models import UserProfile


class EntstreamSignupForm(SignupForm):
    full_name = forms.CharField(
        max_length=150,
        required=True,
        label="Full name",
        widget=forms.TextInput(attrs={
            "placeholder": "Enter your full name",
            "class": "form-input",
        })
    )

    def save(self, request):
        user = super().save(request)

        full_name = self.cleaned_data["full_name"].strip()
        user.first_name = full_name
        user.save(update_fields=["first_name"])

        UserProfile.objects.update_or_create(
            user=user,
            defaults={"full_name": full_name}
        )

        return user


class ProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ["full_name", "username", "bio", "avatar"]
        widgets = {
            "full_name": forms.TextInput(attrs={
                "placeholder": "Your full name",
                "class": "form-input",
            }),
            "username": forms.TextInput(attrs={
                "class": "form-input",
            }),
            "bio": forms.Textarea(attrs={
                "placeholder": "Tell people about yourself",
                "rows": 4,
                "class": "form-input",
            }),
            "avatar": forms.ClearableFileInput(attrs={
                "class": "form-input",
            }),
        }