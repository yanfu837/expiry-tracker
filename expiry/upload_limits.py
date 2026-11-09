"""Stop oversized multipart streams before they fill a temporary filesystem."""
from django.core.exceptions import RequestDataTooBig
from django.core.files.uploadhandler import FileUploadHandler

from document_service.formats import MAX_BYTES


class BoundedUploadHandler(FileUploadHandler):
    def __init__(self, request=None):
        super().__init__(request)
        self.received = 0

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        if self.received > MAX_BYTES:
            raise RequestDataTooBig("File uploads are limited to 10 MB per request.")
        return raw_data

    def file_complete(self, file_size):
        return None
