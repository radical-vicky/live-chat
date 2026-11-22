from django.urls import re_path
from .consumers import (
    DirectMessageConsumer,
    NotificationConsumer,
    RoomChatConsumer,
)

websocket_urlpatterns = [
    re_path(
        r"^ws/chat/(?P<room_name>[0-9a-f-]+)/$",
        RoomChatConsumer.as_asgi(),
    ),
    re_path(
        r"^ws/messages/(?P<user_id>[0-9]+)/$",
        DirectMessageConsumer.as_asgi(),
    ),
    re_path(
        r"^ws/notifications/$",
        NotificationConsumer.as_asgi(),
    ),
]