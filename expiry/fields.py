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
