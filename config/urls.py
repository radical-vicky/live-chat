from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/", admin.site.urls),

    path("accounts/", include("allauth.urls")),
    path("user/", include("accounts.urls")),

    path("", include(("core.urls", "core"), namespace="core")),
    path("videos/", include("streaming.video_urls")),
    path("streams/", include("streaming.urls")),
    path("chat/", include("chat.urls")),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT
    )