from pathlib import Path

from django import forms
from django.conf import settings
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.files.uploadedfile import UploadedFile

from .models import ExpiryItem
from .document_processing import DocumentProcessingError, process_upload, verify_scan_token
from document_service.formats import MAX_BYTES, MIME_TYPES


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


def validate_document(document):
    if not settings.DOCUMENT_UPLOADS_ENABLED:
        raise forms.ValidationError("Document uploads are unavailable. Please contact the site administrator.")
    extension = Path(document.name).suffix.lower()
    if extension not in MIME_TYPES:
        raise forms.ValidationError("This file format is not supported. Use PDF, images, Word, Excel, TXT or CSV.")
    if document.content_type not in MIME_TYPES[extension] | {"application/octet-stream"}:
        raise forms.ValidationError("The file type must match its extension.")
    if document.size > MAX_BYTES:
        raise forms.ValidationError("File size must be 10 MB or smaller.")
    return document


class DocumentAnalysisForm(forms.Form):
    document = forms.FileField()

    def clean_document(self):
        return validate_document(self.cleaned_data["document"])


class ExpiryItemForm(forms.ModelForm):
    scan_token = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["document"].widget.attrs.update({"accept": ",".join(MIME_TYPES)})

    class Meta:
        model = ExpiryItem
        fields = ["name", "expiry_date", "notes", "document"]
        widgets = {
            "expiry_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {"document": "Optional. PDF, PNG, JPG, WEBP, DOC, DOCX, XLS, XLSX, TXT or CSV; maximum 10 MB. New uploads require a clean private virus scan."}

    def clean_document(self):
        document = self.cleaned_data.get("document")
        # None/False mean no upload or clear; FieldFile is an unchanged attachment.
        if not isinstance(document, UploadedFile):
            return document
        validate_document(document)
        try:
            verify_scan_token(document, getattr(self.user, "pk", None), self.data.get("scan_token", ""))
            process_upload(document)
        except DocumentProcessingError as error:
            raise forms.ValidationError(str(error)) from None
        return document
