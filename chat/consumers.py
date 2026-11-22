import json
import uuid
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from .models import ChatMessage, DirectMessage
from streaming.models import LiveSession, SessionParticipant


def _profile_link_data(user):
    profile = getattr(user, "profile", None)
    if profile is None:
        profile, _ = UserProfile.objects.get_or_create(user=user)
    elif not profile.username:
        profile.save(update_fields=["username"])

    return {
        "profile_url": reverse(
            "user_profile",
            kwargs={"username": profile.username},
        ),
        "avatar_url": profile.avatar.url if profile.avatar else "",
    }


class RoomChatConsumer(AsyncJsonWebsocketConsumer):

    async def connect(self):
        self.room_name = uuid.UUID(
    self.scope["url_route"]["kwargs"]["room_name"]
)
        self.group_name = f"chat_{self.room_name.hex}"

        user = self.scope["user"]

        if not user.is_authenticated:
            await self.close(code=4001)
            return

        if not await self.user_can_join(user):
            await self.close(code=4003)
            return

        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name,
        )

        await self.accept()

        history = await self.get_history()

        await self.send_json({
            "type": "history",
            "messages": history,
        })

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name,
            )

    async def receive_json(self, content, **kwargs):
        message = str(content.get("message", "")).strip()

        if not message or len(message) > 1000:
            return

        user = self.scope["user"]

        if not await self.user_can_join(user):
            await self.close(code=4003)
            return

        if not await self.chat_is_enabled():
            await self.send_json({
                "type": "error",
                "message": "The host has disabled chat for this room.",
            })
            return

        saved = await self.save_message(user, message)

        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "chat_message",
                "payload": saved,
            },
        )

    async def chat_message(self, event):
        await self.send_json({
            "type": "message",
            **event["payload"],
        })

    async def session_ended(self, event):
        await self.send_json({"type": "session_ended"})

    async def chat_settings_changed(self, event):
        await self.send_json({
            "type": "chat_settings",
            "enabled": event["enabled"],
        })

    @database_sync_to_async
    def user_can_join(self, user):
        session = LiveSession.objects.filter(
            room_name=self.room_name,
            status="live",
        ).first()

        if not session:
            return False

        if session.host_id == user.id:
            return True

        return SessionParticipant.objects.filter(
            session=session,
            user=user,
            left_at__isnull=True,
        ).exists()

    @database_sync_to_async
    def chat_is_enabled(self):
        return LiveSession.objects.filter(
            room_name=self.room_name,
            status="live",
            chat_enabled=True,
        ).exists()

    @database_sync_to_async
    def get_history(self):
        session = LiveSession.objects.filter(
            room_name=self.room_name,
            status="live",
        ).first()

        if not session:
            return []

        messages = (
            ChatMessage.objects
            .filter(session=session)
            .select_related("user", "user__profile")
            .order_by("-created_at")[:50]
        )

        history = []
        for item in reversed(messages):
            history.append({
                "id": item.id,
                "username": (
                    item.user.profile.full_name
                    if hasattr(item.user, "profile")
                    and item.user.profile.full_name
                    else item.user.get_username()
                ),
                "message": item.message,
                "created_at": item.created_at.isoformat(),
                **_profile_link_data(item.user),
            })
        return history

    @database_sync_to_async
    def save_message(self, user, message):
        session = LiveSession.objects.get(
            room_name=self.room_name,
            status="live",
        )

        item = ChatMessage.objects.create(
            session=session,
            user=user,
            message=message,
        )

        profile = getattr(user, "profile", None)
        username = (
            profile.full_name
            if profile and profile.full_name
            else user.get_username()
        )

        return {
            "id": item.id,
            "username": username,
            "message": item.message,
            "created_at": item.created_at.isoformat(),
            **_profile_link_data(user),
        }


class DirectMessageConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close(code=4001)
            return

        try:
            self.other_user_id = int(
                self.scope["url_route"]["kwargs"]["user_id"]
            )
        except (TypeError, ValueError):
            await self.close(code=4004)
            return

        if self.other_user_id == user.id or not await self.user_exists():
            await self.close(code=4003)
            return

        self.group_name = "dm_%s_%s" % tuple(sorted((
            user.id,
            self.other_user_id,
        )))
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json({
            "type": "history",
            "messages": await self.get_history(),
        })

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name,
            )

    async def receive_json(self, content, **kwargs):
        message = str(content.get("message", "")).strip()
        if not message or len(message) > 1000:
            return
        payload = await self.save_message(message)
        await self.channel_layer.group_send(
            self.group_name,
            {"type": "direct_message", "payload": payload},
        )
        await self.channel_layer.group_send(
            f"user_{self.other_user_id}",
            {
                "type": "notification",
                "payload": payload["notification"],
            },
        )

    async def direct_message(self, event):
        await self.send_json({
            "type": "message",
            **event["payload"],
        })

    @database_sync_to_async
    def user_exists(self):
        from django.contrib.auth import get_user_model

        return get_user_model().objects.filter(
            pk=self.other_user_id,
            is_active=True,
        ).exists()

    @database_sync_to_async
    def get_history(self):
        user = self.scope["user"]
        messages = DirectMessage.objects.filter(
            Q(sender_id=user.id, recipient_id=self.other_user_id)
            | Q(sender_id=self.other_user_id, recipient_id=user.id)
        ).select_related("sender").order_by("-created_at")[:50]
        result = []
        for item in reversed(list(messages)):
            profile = getattr(item.sender, "profile", None)
            result.append({
                "id": item.pk,
                "sender_id": item.sender_id,
                "username": (
                    profile.full_name
                    if profile and profile.full_name
                    else item.sender.get_username()
                ),
                "message": item.message,
                "created_at": item.created_at.isoformat(),
            })
        return result

    @database_sync_to_async
    def save_message(self, message):
        from streaming.models import Notification

        user = self.scope["user"]
        item = DirectMessage.objects.create(
            sender=user,
            recipient_id=self.other_user_id,
            message=message,
        )
        profile = getattr(user, "profile", None)
        username = (
            profile.full_name
            if profile and profile.full_name
            else user.get_username()
        )
        notification = Notification.objects.create(
            recipient_id=self.other_user_id,
            message=f"New message from {username}",
        )
        return {
            "id": item.pk,
            "sender_id": user.id,
            "username": username,
            "message": item.message,
            "created_at": item.created_at.isoformat(),
            "notification": {
                "id": notification.pk,
                "message": notification.message,
                "session_title": "",
                "url": f"/streams/messages/?with={user.id}",
                "created_at": notification.created_at.isoformat(),
            },
        }


class NotificationConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close(code=4001)
            return
        self.group_name = f"user_{user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name,
            )

    async def notification(self, event):
        await self.send_json({
            "type": "notification",
            **event["payload"],
        })