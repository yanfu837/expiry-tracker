from django.db.models.fields.files import FieldFile, FileField
from django.urls import reverse


class ProtectedFieldFile(FieldFile):
    @property
    def url(self):
        self._require_file()
        return reverse("download_document", args=[self.instance.pk])


class ProtectedDocumentField(FileField):
    """All templates and admin widgets link to the owner-checked application URL."""
    attr_class = ProtectedFieldFile

    def pre_save(self, model_instance, add):
        value = getattr(model_instance, self.attname)
        if value and not value._committed:
            from .document_processing import process_upload
            process_upload(value.file)
        return super().pre_save(model_instance, add)
