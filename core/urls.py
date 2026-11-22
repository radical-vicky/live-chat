from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("about/", views.about, name="about"),
    path("admin-overview/", views.admin_overview, name="admin_overview"),
    path("admin-sessions/", views.admin_sessions, name="admin_sessions"),
    path(
        "admin-sessions/<uuid:room_name>/end/",
        views.admin_end_session,
        name="admin_end_session",
    ),
    path(
        "admin-sessions/<uuid:room_name>/remove/",
        views.admin_remove_session,
        name="admin_remove_session",
    ),
]