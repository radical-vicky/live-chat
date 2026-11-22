from django import forms
from django.utils import timezone

from .models import LiveSession, UploadedVideo

class LiveSessionForm(forms.ModelForm):
    recording_consent = forms.BooleanField(
        required=False,
        label="I agree to recording and saving this live session as a replay.",
    )

    class Meta:
        model = LiveSession
        fields = [
            "title",
            "category",
            "description",
            "visibility",
            "scheduled_at",
            "participant_limit",
            "thumbnail",
        ]

        widgets = {
            "title": forms.TextInput(attrs={
                "placeholder": "Give your session a title",
                "class": "form-input",
            }),
            "category": forms.Select(attrs={
                "class": "form-input",
            }),

            "description": forms.Textarea(attrs={
                "placeholder": "What is your session about?",
                "rows": 4,
                "class": "form-input",
            }),

            "visibility": forms.Select(attrs={
                "class": "form-input",
            }),

            "scheduled_at": forms.DateTimeInput(attrs={
                "type": "datetime-local",
                "class": "form-input",
            }),

            "participant_limit": forms.NumberInput(attrs={
                "min": 2,
                "max": 1000,
                "class": "form-input",
            }),

            "thumbnail": forms.ClearableFileInput(attrs={
                "class": "form-input",
            }),
        }

    def clean_participant_limit(self):
        limit = self.cleaned_data["participant_limit"]

        if limit < 2 or limit > 1000:
            raise forms.ValidationError(
                "Participant limit must be between 2 and 1000."
            )

        return limit

    def clean_scheduled_at(self):
       scheduled_at = self.cleaned_data.get("scheduled_at")

       if scheduled_at and scheduled_at <= timezone.now():
        raise forms.ValidationError(
            "Choose a future date and time."
            )

       return scheduled_at


class PreviewDetailsForm(forms.ModelForm):
    class Meta:
        model = LiveSession
        fields = ["title", "description", "thumbnail"]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "form-input",
            }),
            "description": forms.Textarea(attrs={
                "rows": 4,
                "class": "form-input",
            }),
            "thumbnail": forms.ClearableFileInput(attrs={
                "class": "form-input",
            }),
        }


class PreviewCommentForm(forms.Form):
    text = forms.CharField(
        max_length=1000,
        strip=True,
        widget=forms.Textarea(attrs={
            "rows": 3,
            "maxlength": 1000,
            "placeholder": "Write a comment",
        }),
    )


class VideoUploadForm(forms.ModelForm):
    class Meta:
        model = UploadedVideo
        fields = ["title", "description"]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "form-input",
                "maxlength": 150,
                "placeholder": "Give your video a title",
            }),
            "description": forms.Textarea(attrs={
                "class": "form-input",
                "rows": 4,
                "placeholder": "What is this video about?",
            }),
        }