from django.urls import path
from . import views

urlpatterns = [
    path("profile/", views.profile, name="profile"),
    path(
        "profile/<slug:username>/",
        views.user_profile,
        name="user_profile",
    ),
]