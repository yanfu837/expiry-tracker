from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .deletions import queue_document_deletion
from .models import ExpiryItem


@receiver(pre_save, sender=ExpiryItem)
def remember_previous_document(sender, instance, **kwargs):
    instance._previous_document = ""
    if instance.pk:
        instance._previous_document = sender.objects.filter(pk=instance.pk).values_list("document", flat=True).first() or ""


@receiver(post_save, sender=ExpiryItem)
def delete_replaced_document(sender, instance, **kwargs):
    previous = getattr(instance, "_previous_document", "")
    if previous and previous != instance.document.name:
        queue_document_deletion(previous)


@receiver(post_delete, sender=ExpiryItem)
def delete_item_document(sender, instance, **kwargs):
    if instance.document:
        queue_document_deletion(instance.document.name)
