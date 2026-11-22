from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from streaming.models import LiveSession
from streaming.services import PreviewRecordingError


class AdminPreviewLifecycleTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="test-password",
            is_staff=True,
        )
        self.host = user_model.objects.create_user(
            username="host",
            email="host@example.com",
            password="test-password",
        )
        self.session = LiveSession.objects.create(
            host=self.host,
            title="Recorded session",
            status="live",
            preview_status="recording",
            preview_egress_id="egress-123",
        )
        self.client.force_login(self.admin)

    def test_admin_can_end_live_session_when_replay_finalization_fails(self):
        with self.assertLogs("core.views", level="ERROR"):
            with patch(
                "core.views.stop_preview_recording",
                side_effect=PreviewRecordingError("egress unavailable"),
            ):
                response = self.client.post(
                    reverse(
                        "core:admin_end_session",
                        args=[self.session.room_name],
                    ),
                    follow=True,
                )

        self.assertRedirects(response, reverse("core:admin_sessions"))
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "ended")
        self.assertEqual(self.session.preview_status, "failed")
        self.assertContains(response, "Session ended, but its replay could not be saved.")

    def test_admin_removal_deletes_replay_object_before_session(self):
        self.session.status = "ended"
        self.session.preview_status = "ready"
        self.session.preview_object_key = "previews/test.mp4"
        self.session.save(update_fields=[
            "status",
            "preview_status",
            "preview_object_key",
        ])

        with patch("core.views.delete_preview_recording") as delete_recording:
            response = self.client.post(
                reverse(
                    "core:admin_remove_session",
                    args=[self.session.room_name],
                ),
                follow=True,
            )

        self.assertRedirects(response, reverse("core:admin_sessions"))
        delete_recording.assert_called_once()
        self.assertFalse(
            LiveSession.objects.filter(pk=self.session.pk).exists()
        )
