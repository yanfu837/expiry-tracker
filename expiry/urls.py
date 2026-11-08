from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("add/", views.add_item, name="add_item"),
    path("edit/<int:item_id>/", views.edit_item, name="edit_item"),
    path("delete/<int:item_id>/", views.delete_item, name="delete_item"),
    path("documents/<int:item_id>/download/", views.download_document, name="download_document"),
    path("register/", views.register, name="register"),
    path("account/email/", views.account_email, name="account_email"),
    path("login/", auth_views.LoginView.as_view(template_name="expiry/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password-reset/", views.ConfiguredPasswordResetView.as_view(
        template_name="expiry/password_reset_form.html",
        email_template_name="expiry/password_reset_email.txt",
        subject_template_name="expiry/password_reset_subject.txt",
        success_url=reverse_lazy("password_reset_done"),
    ), name="password_reset"),
    path("password-reset/done/", auth_views.PasswordResetDoneView.as_view(
        template_name="expiry/password_reset_done.html",
    ), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="expiry/password_reset_confirm.html",
        success_url=reverse_lazy("password_reset_complete"),
    ), name="password_reset_confirm"),
    path("reset/done/", auth_views.PasswordResetCompleteView.as_view(
        template_name="expiry/password_reset_complete.html",
    ), name="password_reset_complete"),
    path("test-email/", views.test_email, name="test_email"),
    path("privacy/", views.privacy, name="privacy"),
]
