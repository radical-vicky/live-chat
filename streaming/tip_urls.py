from django.urls import path

from . import tip_views


urlpatterns = [
    path("tips/", tip_views.create_mpesa_tip, name="mpesa_tip_create"),
    path(
        "tips/<uuid:tip_id>/status/",
        tip_views.mpesa_tip_status,
        name="mpesa_tip_status",
    ),
    path(
        "mpesa/callback/<str:token>/",
        tip_views.mpesa_tip_callback,
        name="mpesa_tip_callback",
    ),
]
