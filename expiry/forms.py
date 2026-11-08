from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.files.uploadedfile import UploadedFile

from .models import ExpiryItem


class RegistrationForm(UserCreationForm):
    email = forms.EmailField(help_text="Used for expiry reminders and password recovery.")

    class Meta(UserCreationForm.Meta):
        fields = ("username", "email")


class AccountEmailForm(forms.ModelForm):
    email = forms.EmailField(help_text="Used for expiry reminders and password recovery.")
    current_password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    class Meta:
        model = User
        fields = ("email",)

    def clean_current_password(self):
        password = self.cleaned_data["current_password"]
        if not self.instance.check_password(password):
            raise forms.ValidationError("Your current password is incorrect.")
        return password


class ExpiryItemForm(forms.ModelForm):
    class Meta:
        model = ExpiryItem
        fields = ["name", "expiry_date", "notes", "document"]
        widgets = {
            "expiry_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {"document": "Optional. PDF, PNG, JPG, DOC, DOCX, XLS or XLSX; maximum 10 MB."}

    def clean_document(self):
        document = self.cleaned_data.get("document")
        # None/False mean no upload or clear; FieldFile is an unchanged attachment.
        if not isinstance(document, UploadedFile):
            return document
        if not settings.DOCUMENT_UPLOADS_ENABLED:
            raise forms.ValidationError("Document uploads are unavailable. Please contact the site administrator.")

        allowed_types = {
            ".pdf": {"application/pdf"},
            ".png": {"image/png"},
            ".jpg": {"image/jpeg"},
            ".jpeg": {"image/jpeg"},
            ".doc": {"application/msword"},
            ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
            ".xls": {"application/vnd.ms-excel"},
            ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        }
        extension = Path(document.name).suffix.lower()
        if extension not in allowed_types:
            raise forms.ValidationError("Only PDF, PNG, JPG, JPEG, DOC, DOCX, XLS and XLSX files are allowed.")
        if document.content_type not in allowed_types[extension]:
            raise forms.ValidationError("The file type must match its extension.")
        if document.size > 10 * 1024 * 1024:
            raise forms.ValidationError("File size must be 10 MB or smaller.")
        return document
