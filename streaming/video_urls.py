from django.urls import path

from . import video_views


urlpatterns = [
    path("", video_views.video_library, name="video_library"),
    path("upload/", video_views.video_upload, name="video_upload"),
    path(
        "upload/signature/",
        video_views.video_upload_signature,
        name="video_upload_signature",
    ),
    path(
        "<int:video_id>/",
        video_views.video_detail,
        name="video_detail",
    ),
    path(
        "<int:video_id>/delete/",
        video_views.delete_video,
        name="delete_video",
    ),
]
