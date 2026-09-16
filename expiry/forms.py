from django import forms
from .models import ExpiryItem


class ExpiryItemForm(forms.ModelForm):
    class Meta:
        model = ExpiryItem
        fields = ["name", "expiry_date", "notes", "document"]

    def clean_document(self):
        document = self.cleaned_data.get("document")

        if document:
            allowed_extensions = ["pdf", "png", "jpg", "jpeg","doc",
    "docx",
    "xls",
    "xlsx",]

            extension = document.name.split(".")[-1].lower()

            if extension not in allowed_extensions:
                raise forms.ValidationError(
                    "Only PDF, PNG, JPG, JPEG, DOC, DOCX, XLS, and XLSX files are allowed."
)
            allowed_mime_types = [
        "application/pdf",
        "image/png",
        "image/jpeg",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ]

        if document.content_type not in allowed_mime_types:
            raise forms.ValidationError(
                "Invalid file type."
            )


            if document.size > 10 * 1024 * 1024:
                raise forms.ValidationError(
                    "File size must be 10 MB or smaller."
                )

        return document