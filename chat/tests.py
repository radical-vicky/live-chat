from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TransactionTestCase
from django.urls import reverse

from config.asgi import application
from streaming.models import LiveSession, Notification

from .models import ChatMessage, DirectMessage


class DirectMessageRealtimeTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        user_model = get_user_model()
        self.sender = user_model.objects.create_user(
            username="sender",
            email="sender@example.com",
            password="test-password",
        )
        self.recipient = user_model.objects.create_user(
            username="recipient",
            email="recipient@example.com",
            password="test-password",
        )
        self.sender_cookie = self._session_cookie(self.sender)
        self.recipient_cookie = self._session_cookie(self.recipient)

    def _session_cookie(self, user):
        client = Client()
        client.force_login(user)
        return client.cookies[settings.SESSION_COOKIE_NAME].value

    def test_websocket_persists_and_delivers_direct_messages_and_notifications(self):
        async def exercise():
            cookie_name = settings.SESSION_COOKIE_NAME.encode()
            sender_socket = WebsocketCommunicator(
                application,
                f"/ws/messages/{self.recipient.pk}/",
                headers=[
                    (b"cookie", cookie_name + b"=" + self.sender_cookie.encode()),
                    (b"origin", b"http://testserver"),
                    (b"host", b"testserver"),
                ],
            )
            recipient_socket = WebsocketCommunicator(
                application,
                f"/ws/messages/{self.sender.pk}/",
                headers=[
                    (b"cookie", cookie_name + b"=" + self.recipient_cookie.encode()),
                    (b"origin", b"http://testserver"),
                    (b"host", b"testserver"),
                ],
            )
            notifications_socket = WebsocketCommunicator(
                application,
                "/ws/notifications/",
                headers=[
                    (b"cookie", cookie_name + b"=" + self.recipient_cookie.encode()),
                    (b"origin", b"http://testserver"),
                    (b"host", b"testserver"),
                ],
            )

            connected, result = await sender_socket.connect()
            self.assertTrue(connected, f"Sender websocket was rejected: {result}")
            connected, result = await recipient_socket.connect()
            self.assertTrue(connected, f"Recipient websocket was rejected: {result}")
            connected, result = await notifications_socket.connect()
            self.assertTrue(connected, f"Notification websocket was rejected: {result}")
            await sender_socket.receive_json_from()
            await recipient_socket.receive_json_from()

            await sender_socket.send_json_to({"message": "Hello in real time"})
            sent_message = await sender_socket.receive_json_from()
            received_message = await recipient_socket.receive_json_from()
            notification = await notifications_socket.receive_json_from()

            self.assertEqual(sent_message["message"], "Hello in real time")
            self.assertEqual(received_message["sender_id"], self.sender.pk)
            self.assertEqual(notification["type"], "notification")
            self.assertIn("sender", notification["message"])

            await sender_socket.disconnect()
            await recipient_socket.disconnect()
            await notifications_socket.disconnect()

        async_to_sync(exercise)()
        self.assertEqual(DirectMessage.objects.count(), 1)
        self.assertEqual(
            Notification.objects.filter(recipient=self.recipient).count(),
            1,
        )


class RoomChatControlTests(TransactionTestCase):
    def test_room_chat_history_and_messages_include_clickable_profiles(self):
        user = get_user_model().objects.create_user(
            username="chat-host",
            email="chat-host@example.com",
            password="test-password",
        )
        session = LiveSession.objects.create(
            host=user,
            title="Profile links in chat",
            status="live",
        )
        old_message = ChatMessage.objects.create(
            session=session,
            user=user,
            message="Earlier message",
        )
        cookie_client = Client()
        cookie_client.force_login(user)
        session_cookie = cookie_client.cookies[
            settings.SESSION_COOKIE_NAME
        ].value
        profile_url = reverse(
            "user_profile",
            kwargs={"username": user.profile.username},
        )

        async def exercise():
            communicator = WebsocketCommunicator(
                application,
                f"/ws/chat/{session.room_name}/",
                headers=[
                    (
                        b"cookie",
                        settings.SESSION_COOKIE_NAME.encode()
                        + b"="
                        + session_cookie.encode(),
                    ),
                    (b"origin", b"http://testserver"),
                    (b"host", b"testserver"),
                ],
            )
            connected, result = await communicator.connect()
            self.assertTrue(connected, f"Room chat was rejected: {result}")
            history = await communicator.receive_json_from()
            history_message = history["messages"][0]
            self.assertEqual(history_message["id"], old_message.id)
            self.assertEqual(history_message["profile_url"], profile_url)
            self.assertEqual(history_message["avatar_url"], "")

            await communicator.send_json_to({"message": "New message"})
            message = await communicator.receive_json_from()
            self.assertEqual(message["profile_url"], profile_url)
            self.assertEqual(message["avatar_url"], "")
            await communicator.disconnect()

        async_to_sync(exercise)()
        self.assertEqual(ChatMessage.objects.count(), 2)

    def test_disabled_room_chat_rejects_messages(self):
        user = get_user_model().objects.create_user(
            username="room-host",
            email="room-host@example.com",
            password="test-password",
        )
        session = LiveSession.objects.create(
            host=user,
            title="Locked chat",
            status="live",
            chat_enabled=False,
        )
        cookie_client = Client()
        cookie_client.force_login(user)
        session_cookie = cookie_client.cookies[
            settings.SESSION_COOKIE_NAME
        ].value

        async def exercise():
            communicator = WebsocketCommunicator(
                application,
                f"/ws/chat/{session.room_name}/",
                headers=[
                    (
                        b"cookie",
                        settings.SESSION_COOKIE_NAME.encode()
                        + b"="
                        + session_cookie.encode(),
                    ),
                    (b"origin", b"http://testserver"),
                    (b"host", b"testserver"),
                ],
            )
            connected, result = await communicator.connect()
            self.assertTrue(connected, f"Room chat was rejected: {result}")
            await communicator.receive_json_from()
            await communicator.send_json_to({"message": "Should be blocked"})
            response = await communicator.receive_json_from()
            self.assertEqual(response["type"], "error")
            self.assertIn("disabled", response["message"])
            await communicator.disconnect()

        async_to_sync(exercise)()
        self.assertFalse(ChatMessage.objects.exists())
