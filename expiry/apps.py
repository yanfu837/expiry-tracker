from django.apps import AppConfig


class ExpiryConfig(AppConfig):
    name = 'expiry'

    def ready(self):
        from . import signals  # noqa: F401
