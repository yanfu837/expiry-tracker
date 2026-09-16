from django.urls import path
from . import views
from django.contrib.auth import views as auth_views



urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("add/", views.add_item, name="add_item"),
    path("edit/<int:item_id>/", views.edit_item, name="edit_item"),
    path("delete/<int:item_id>/", views.delete_item, name="delete_item"),
    path("register/", views.register, name="register"),

path(
    "login/",
    auth_views.LoginView.as_view(template_name="expiry/login.html"),
    name="login"
),

path(
    "logout/",
    auth_views.LogoutView.as_view(),
    name="logout"
),
path("test-email/", views.test_email, name="test_email"),




]
