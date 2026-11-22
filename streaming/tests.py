from datetime import timedelta
import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.test import override_settings
from livekit import api

from accounts.models import UserProfile

from .services import (
    PreviewRecordingError,
    start_preview_recording,
    stop_preview_recording,
)
from .models import (
    CreatorFollow,
    FavoriteSession,
    LiveSession,
    MpesaTip,
    Notification,
    PreviewComment,
    PreviewLike,
    SessionParticipant,
    SessionReminder,
    UploadedVideo,
)


class LiveSessionWorkflowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="host",
            email="host@example.com",
            password="test-password",
        )
        self.viewer = user_model.objects.create_user(
            username="viewer",
            email="viewer@example.com",
            password="test-password",
        )
        self.session = LiveSession.objects.create(
            host=self.user,
            title="Python Bootcamp",
            description="Learn Python together",
            category="education",
            status="scheduled",
            scheduled_at=timezone.now() + timedelta(days=1),
        )
        self.client.force_login(self.viewer)

    def test_create_session_defaults_visibility_to_public_radio_option(self):
        response = self.client.get(reverse("create_session"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'name="visibility" value="public" checked required',
        )
        self.assertContains(response, 'name="visibility" value="private"')

    def test_explore_search_and_category_filters_database_sessions(self):
        response = self.client.get(
            reverse("explore_sessions"),
            {"q": "Python", "category": "education"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Python Bootcamp")
        self.assertEqual(list(response.context["sessions"]), [self.session])

    def test_explore_session_card_uses_uploaded_thumbnail(self):
        self.session.thumbnail = "stream_thumbnails/preview.jpg"
        self.session.save(update_fields=["thumbnail"])

        response = self.client.get(reverse("explore_sessions"), {"q": "Python"})

        self.assertContains(response, 'class="session-thumbnail"')
        self.assertContains(response, "/media/stream_thumbnails/preview.jpg")

    def test_explore_lists_matching_uploaded_videos(self):
        video = UploadedVideo.objects.create(
            owner=self.user,
            title="Python video tutorial",
            cloudinary_public_id=f"radevlive/videos/user_{self.user.pk}/{uuid.uuid4().hex}",
            video_url="https://res.cloudinary.com/demo/video/upload/tutorial.mp4",
            thumbnail_url="https://res.cloudinary.com/demo/video/upload/tutorial.jpg",
            format="mp4",
            size_bytes=1024,
        )

        response = self.client.get(reverse("explore_sessions"), {"q": "Python"})

        self.assertContains(response, video.title)
        self.assertContains(response, f'<img src="{video.thumbnail_url}"')
        self.assertContains(
            response,
            reverse("video_detail", args=[video.pk]),
        )
        self.assertEqual(list(response.context["videos"]), [video])

    def test_dashboard_live_now_cards_use_uploaded_thumbnail(self):
        self.session.status = "live"
        self.session.thumbnail = "stream_thumbnails/live.jpg"
        self.session.save(update_fields=["status", "thumbnail"])

        response = self.client.get(reverse("core:dashboard"))

        self.assertContains(response, 'class="live-now-thumbnail"')
        self.assertContains(response, "/media/stream_thumbnails/live.jpg")

    def test_dashboard_lists_recent_uploaded_videos(self):
        video = UploadedVideo.objects.create(
            owner=self.user,
            title="Dashboard video",
            cloudinary_public_id=f"radevlive/videos/user_{self.user.pk}/{uuid.uuid4().hex}",
            video_url="https://res.cloudinary.com/demo/video/upload/dashboard.mp4",
            thumbnail_url="https://res.cloudinary.com/demo/video/upload/dashboard.jpg",
            format="mp4",
            size_bytes=1024,
        )

        response = self.client.get(reverse("core:dashboard"))

        self.assertContains(response, video.title)
        self.assertContains(response, f'<img src="{video.thumbnail_url}"')
        self.assertContains(response, reverse("video_detail", args=[video.pk]))
        self.assertEqual(list(response.context["videos"]), [video])

    def test_public_home_lists_recent_uploaded_videos(self):
        video = UploadedVideo.objects.create(
            owner=self.user,
            title="Home page video",
            cloudinary_public_id=f"radevlive/videos/user_{self.user.pk}/{uuid.uuid4().hex}",
            video_url="https://res.cloudinary.com/demo/video/upload/home.mp4",
            thumbnail_url="https://res.cloudinary.com/demo/video/upload/home.jpg",
            format="mp4",
            size_bytes=1024,
        )

        response = self.client.get(reverse("core:home"))

        self.assertContains(response, video.title)
        self.assertContains(response, f'<img src="{video.thumbnail_url}"')
        self.assertContains(response, reverse("video_detail", args=[video.pk]))
        self.assertEqual(list(response.context["videos"]), [video])

    def test_favorite_and_reminder_actions_persist_and_toggle(self):
        favorite_url = reverse(
            "toggle_favorite",
            args=[self.session.room_name],
        )
        reminder_url = reverse(
            "toggle_reminder",
            args=[self.session.room_name],
        )

        self.client.post(favorite_url)
        self.client.post(reminder_url)
        self.assertTrue(FavoriteSession.objects.filter(
            user=self.viewer,
            session=self.session,
        ).exists())
        self.assertTrue(SessionReminder.objects.filter(
            user=self.viewer,
            session=self.session,
        ).exists())
        self.assertContains(
            self.client.get(reverse("favorites")),
            "Python Bootcamp",
        )
        self.assertContains(
            self.client.get(reverse("upcoming_sessions")),
            "Cancel reminder",
        )


        self.client.post(favorite_url)
        self.client.post(reminder_url)
        self.assertFalse(FavoriteSession.objects.exists())
        self.assertFalse(SessionReminder.objects.exists())

    def test_favorite_for_unavailable_session_redirects_with_notice(self):
        response = self.client.post(
            reverse("toggle_favorite", args=[uuid.uuid4()]),
            follow=True,
        )

        self.assertRedirects(response, reverse("favorites"))
        self.assertContains(response, "That session is no longer available.")

    def test_meeting_code_resolves_to_existing_session(self):
        response = self.client.post(
            reverse("join_meeting"),
            {"code": str(self.session.room_name)},
        )

        self.assertRedirects(
            response,
            reverse("session_detail", args=[self.session.room_name]),
        )

    def test_create_live_starts_session_and_schedule_requires_a_time(self):
        CreatorFollow.objects.create(
            follower=self.user,
            following=self.viewer,
        )
        with patch("streaming.views.check_preview_recording_settings"):
            live_response = self.client.post(
                reverse("create_session"),
                {
                    "title": "Design Critique",
                    "category": "business",
                    "description": "Review designs",
                    "visibility": "public",
                    "participant_limit": 20,
                    "recording_consent": "on",
                    "action": "start",
                },
            )
        live_session = LiveSession.objects.get(title="Design Critique")
        self.assertRedirects(
            live_response,
            reverse("session_detail", args=[live_session.room_name]),
        )
        self.assertEqual(live_session.status, "live")
        self.assertIsNotNone(live_session.host_recording_consented_at)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.user,
                session=live_session,
            ).exists()
        )

        invalid_schedule = self.client.post(
            reverse("create_session"),
            {
                "title": "No date",
                "category": "education",
                "description": "",
                "visibility": "public",
                "participant_limit": 20,
                "action": "schedule",
            },
        )
        self.assertEqual(invalid_schedule.status_code, 200)
        self.assertFalse(LiveSession.objects.filter(title="No date").exists())

    def test_create_live_requires_explicit_recording_consent(self):
        response = self.client.post(
            reverse("create_session"),
            {
                "title": "Unconsented live",
                "category": "education",
                "description": "",
                "visibility": "public",
                "participant_limit": 20,
                "action": "start",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            LiveSession.objects.filter(title="Unconsented live").exists()
        )
        self.assertContains(response, "Agree to the recording notice")

    def test_create_live_is_blocked_when_replay_storage_is_unconfigured(self):
        with patch(
            "streaming.views.check_preview_recording_settings",
            side_effect=PreviewRecordingError(
                "Replay recording is not configured. Set "
                "LIVEKIT_RECORDING_S3_BUCKET, "
                "LIVEKIT_RECORDING_S3_ACCESS_KEY, and "
                "LIVEKIT_RECORDING_S3_SECRET in the server environment."
            ),
        ):
            response = self.client.post(
                reverse("create_session"),
                {
                    "title": "Unrecorded live",
                    "category": "education",
                    "description": "",
                    "visibility": "public",
                    "participant_limit": 20,
                    "recording_consent": "on",
                    "action": "start",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "LIVEKIT_RECORDING_S3_BUCKET",
        )
        self.assertContains(
            response,
            "LIVEKIT_RECORDING_S3_ACCESS_KEY",
        )
        self.assertContains(
            response,
            "LIVEKIT_RECORDING_S3_SECRET",
        )
        self.assertFalse(
            LiveSession.objects.filter(title="Unrecorded live").exists()
        )

    def test_create_live_repairs_a_legacy_user_without_a_profile(self):
        UserProfile.objects.filter(user=self.viewer).delete()

        with patch("streaming.views.check_preview_recording_settings"):
            response = self.client.post(
                reverse("create_session"),
                {
                    "title": "Community Live",
                    "category": "business",
                    "description": "",
                    "visibility": "public",
                    "participant_limit": 20,
                    "recording_consent": "on",
                    "action": "start",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(UserProfile.objects.filter(user=self.viewer).exists())
        self.assertEqual(
            LiveSession.objects.get(title="Community Live").status,
            "live",
        )

    def test_starting_a_session_creates_live_notifications_for_saved_users(self):
        FavoriteSession.objects.create(user=self.viewer, session=self.session)
        SessionReminder.objects.create(user=self.viewer, session=self.session)
        CreatorFollow.objects.create(
            follower=self.viewer,
            following=self.user,
        )

        self.client.force_login(self.user)
        with patch("streaming.views.check_preview_recording_settings"):
            response = self.client.post(
                reverse("start_session", args=[self.session.room_name]),
                {"recording_consent": "on"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.viewer,
                session=self.session,
            ).count(),
            1,
        )

    def test_locked_live_room_rejects_new_participants(self):
        self.session.status = "live"
        self.session.is_locked = True
        self.session.save(update_fields=["status", "is_locked"])

        response = self.client.post(
            reverse("join_session", args=[self.session.room_name]),
        )

        self.assertRedirects(
            response,
            reverse("session_detail", args=[self.session.room_name]),
        )
        self.assertFalse(
            SessionParticipant.objects.filter(
                session=self.session,
                user=self.viewer,
            ).exists()
        )

    def test_host_room_renders_controls_and_participant_panel(self):
        self.session.status = "live"
        self.session.save(update_fields=["status"])
        self.client.force_login(self.user)

        join_url = reverse("join_session", args=[self.session.room_name])
        consent_response = self.client.get(join_url)
        self.assertContains(consent_response, "Recording notice")
        response = self.client.post(
            join_url,
            {"recording_consent": "on"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Host controls")
        self.assertContains(response, "Mute all")
        self.assertContains(response, "Disable chat")
        self.assertContains(response, "Lock room")
        self.assertContains(response, "Participants (")

    @override_settings(
        MPESA_API_BASE_URL="https://sandbox.safaricom.co.ke",
        MPESA_CONSUMER_KEY="test-consumer-key",
        MPESA_CONSUMER_SECRET="test-consumer-secret",
        MPESA_SHORTCODE="174379",
        MPESA_PASSKEY="test-passkey",
        MPESA_CALLBACK_BASE_URL="https://radev.example",
        MPESA_CALLBACK_TOKEN="test-callback-token",
    )
    def test_room_shows_tip_controls_to_live_participants(self):
        self.session.status = "live"
        self.session.preview_status = "recording"
        self.session.host_recording_consented_at = timezone.now()
        self.session.save(update_fields=[
            "status",
            "preview_status",
            "host_recording_consented_at",
        ])

        response = self.client.post(
            reverse("join_session", args=[self.session.room_name]),
            {"recording_consent": "on"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tip host with M-Pesa")
        self.assertContains(response, 'value="live_session"')

    def test_room_participants_include_clickable_profile_and_avatar_data(self):
        self.session.status = "live"
        self.session.save(update_fields=["status"])
        participant = SessionParticipant.objects.create(
            session=self.session,
            user=self.viewer,
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("room_status", args=[self.session.room_name]),
        )

        self.assertEqual(response.status_code, 200)
        participant_data = next(
            item
            for item in response.json()["participants"]
            if item["id"] == self.viewer.id
        )
        self.assertEqual(
            participant_data["profile_url"],
            reverse(
                "user_profile",
                kwargs={"username": self.viewer.profile.username},
            ),
        )
        self.assertEqual(participant_data["avatar_url"], "")
        self.assertEqual(
            participant_data["livekit_identity"],
            participant.livekit_identity,
        )
        self.assertFalse(participant_data["is_self"])
        self.assertEqual(
            self.client.get(participant_data["profile_url"]).status_code,
            200,
        )

    def test_session_detail_shows_clickable_participant_avatars(self):
        self.session.status = "live"
        self.session.save(update_fields=["status"])
        SessionParticipant.objects.create(
            session=self.session,
            user=self.viewer,
        )
        UserProfile.objects.filter(user=self.viewer).update(
            avatar="profiles/viewer.png",
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("session_detail", args=[self.session.room_name]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse(
                "user_profile",
                kwargs={"username": self.viewer.profile.username},
            ),
        )
        avatar_url = UserProfile.objects.get(user=self.viewer).avatar.url
        self.assertContains(response, avatar_url)
        self.assertContains(response, "View viewer's profile")

    def test_session_detail_shows_full_thumbnail_without_cropping(self):
        self.session.thumbnail = "stream_thumbnails/session-full.jpg"
        self.session.save(update_fields=["thumbnail"])

        response = self.client.get(
            reverse("session_detail", args=[self.session.room_name]),
        )

        self.assertContains(
            response,
            '<img class="hero-session-image" src="/media/stream_thumbnails/session-full.jpg" alt="Python Bootcamp session image">',
            html=False,
        )
        self.assertContains(response, "object-fit:contain")
        self.assertContains(
            response,
            "grid-template-columns:minmax(120px,38%) minmax(0,1fr)",
        )
        self.assertContains(response, 'class="session-extra-details"')
        self.assertContains(response, "About")
        self.assertContains(response, "Participants")
        self.assertContains(response, self.session.description)

    def test_participant_must_consent_before_entering_a_recorded_room(self):
        self.session.status = "live"
        self.session.preview_status = "recording"
        self.session.host_recording_consented_at = timezone.now()
        self.session.save(update_fields=[
            "status",
            "preview_status",
            "host_recording_consented_at",
        ])
        join_url = reverse("join_session", args=[self.session.room_name])

        consent_response = self.client.get(join_url)
        self.assertContains(consent_response, "recorded live room")
        self.assertFalse(
            SessionParticipant.objects.filter(
                session=self.session,
                user=self.viewer,
            ).exists()
        )

        response = self.client.post(
            join_url,
            {"recording_consent": "on"},
        )

        self.assertEqual(response.status_code, 200)
        participant = SessionParticipant.objects.get(
            session=self.session,
            user=self.viewer,
        )
        self.assertIsNotNone(participant.recording_consented_at)

    def test_ended_preview_supports_likes_comments_favorites_and_playback(self):
        self.session.status = "ended"
        self.session.preview_status = "ready"
        self.session.preview_object_key = "previews/test.mp4"
        self.session.save(update_fields=[
            "status",
            "preview_status",
            "preview_object_key",
        ])
        detail_url = reverse("session_detail", args=[self.session.room_name])
        with patch(
            "streaming.views.preview_video_url",
            return_value="https://media.example/preview.mp4",
        ):
            detail_response = self.client.get(detail_url)
        self.assertContains(detail_response, "preview.mp4")
        self.assertContains(
            self.client.get(reverse("explore_sessions")),
            "Replay previews",
        )
        creator_url = reverse(
            "creator_profile",
            args=[self.user.profile.username],
        )
        user_profile_url = reverse(
            "user_profile",
            kwargs={"username": self.user.profile.username},
        )
        self.assertRedirects(self.client.get(creator_url), user_profile_url)
        creator_response = self.client.get(creator_url, follow=True)
        self.assertContains(creator_response, "Python Bootcamp")

        self.client.post(
            reverse("toggle_preview_like", args=[self.session.room_name]),
        )
        self.client.post(
            reverse("add_preview_comment", args=[self.session.room_name]),
            {"text": "Great session!"},
        )
        self.client.post(
            reverse("toggle_favorite", args=[self.session.room_name]),
        )
        self.client.post(
            reverse("toggle_follow", args=[self.user.pk]),
        )

        self.assertTrue(
            PreviewLike.objects.filter(
                user=self.viewer,
                session=self.session,
            ).exists()
        )
        self.assertTrue(
            PreviewComment.objects.filter(
                user=self.viewer,
                session=self.session,
                text="Great session!",
            ).exists()
        )
        self.assertTrue(
            FavoriteSession.objects.filter(
                user=self.viewer,
                session=self.session,
            ).exists()
        )
        self.assertTrue(
            CreatorFollow.objects.filter(
                follower=self.viewer,
                following=self.user,
            ).exists()
        )
        self.assertContains(self.client.get(reverse("favorites")), "Python Bootcamp")

    def test_owner_can_edit_and_delete_preview(self):
        self.session.status = "ended"
        self.session.preview_status = "ready"
        self.session.preview_object_key = "previews/test.mp4"
        self.session.save(update_fields=[
            "status",
            "preview_status",
            "preview_object_key",
        ])
        PreviewLike.objects.create(user=self.viewer, session=self.session)
        PreviewComment.objects.create(
            user=self.viewer,
            session=self.session,
            text="Keep this with the preview",
        )
        self.client.force_login(self.user)

        edit_response = self.client.post(
            reverse("edit_preview", args=[self.session.room_name]),
            {
                "title": "Updated session title",
                "description": "Updated preview description",
            },
        )
        with patch(
            "streaming.views.preview_video_url",
            return_value="",
        ):
            self.assertRedirects(
                edit_response,
                reverse("session_detail", args=[self.session.room_name]),
            )
        self.session.refresh_from_db()
        self.assertEqual(self.session.title, "Updated session title")

        with patch("streaming.views.delete_preview_recording") as delete_recording:
            delete_response = self.client.post(
                reverse("delete_preview", args=[self.session.room_name]),
            )
        self.assertRedirects(
            delete_response,
            reverse("session_detail", args=[self.session.room_name]),
        )
        delete_recording.assert_called_once()
        self.session.refresh_from_db()
        self.assertEqual(self.session.preview_status, "deleted")
        self.assertEqual(self.session.preview_object_key, "")
        self.assertFalse(PreviewLike.objects.filter(session=self.session).exists())
        self.assertFalse(PreviewComment.objects.filter(session=self.session).exists())

    def test_host_can_end_a_live_session_if_replay_finalization_fails(self):
        self.session.status = "live"
        self.session.preview_status = "recording"
        self.session.preview_egress_id = "egress-123"
        self.session.save(update_fields=[
            "status",
            "preview_status",
            "preview_egress_id",
        ])
        self.client.force_login(self.user)

        with self.assertLogs("streaming.views", level="ERROR"):
            with patch(
                "streaming.views.stop_preview_recording",
                side_effect=PreviewRecordingError("recording stop failed"),
            ):
                response = self.client.post(
                    reverse("end_session", args=[self.session.room_name]),
                    follow=True,
                )

        self.assertRedirects(
            response,
            reverse("session_detail", args=[self.session.room_name]),
        )
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, "ended")
        self.assertEqual(self.session.preview_status, "failed")
        self.assertContains(response, "replay could not be saved")

    def test_livekit_egress_saves_and_finalizes_the_session_replay(self):
        class FakeEgress:
            def __init__(self):
                self.start_request = None

            async def start_room_composite_egress(self, request):
                self.start_request = request
                return api.EgressInfo(
                    egress_id="egress-123",
                    status=api.EGRESS_ACTIVE,
                )

            async def stop_egress(self, request):
                self.stop_request = request
                return api.EgressInfo(
                    egress_id=request.egress_id,
                    status=api.EGRESS_COMPLETE,
                )

        class FakeLiveKitAPI:
            def __init__(self, **kwargs):
                self.egress = fake_egress

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_value, traceback):
                return None

        fake_egress = FakeEgress()
        with override_settings(
            LIVEKIT_URL="wss://livekit.example",
            LIVEKIT_API_KEY="test-key",
            LIVEKIT_API_SECRET="test-secret",
            LIVEKIT_RECORDING_S3_BUCKET="recordings",
            LIVEKIT_RECORDING_S3_REGION="us-east-1",
            LIVEKIT_RECORDING_S3_ACCESS_KEY="storage-key",
            LIVEKIT_RECORDING_S3_SECRET="storage-secret",
            LIVEKIT_RECORDING_S3_ENDPOINT="",
            LIVEKIT_RECORDING_S3_FORCE_PATH_STYLE=False,
        ):
            with patch(
                "streaming.services.api.LiveKitAPI",
                FakeLiveKitAPI,
            ), patch("streaming.services._recording_s3_client") as s3_client:
                s3_client.return_value.head_object.return_value = {}
                start_preview_recording(self.session)

                self.assertEqual(
                    fake_egress.start_request.room_name,
                    self.session.room_name.hex,
                )
                self.assertEqual(
                    fake_egress.start_request.file_outputs[0].s3.bucket,
                    "recordings",
                )
                self.assertEqual(self.session.preview_status, "recording")

                stop_preview_recording(self.session)

        self.session.refresh_from_db()
        self.assertEqual(self.session.preview_status, "ready")
        self.assertEqual(self.session.preview_egress_id, "egress-123")

    def test_host_can_toggle_lock_and_chat_settings(self):
        self.session.status = "live"
        self.session.save(update_fields=["status"])
        self.client.force_login(self.user)
        url = reverse("room_controls", args=[self.session.room_name])

        lock_response = self.client.post(url, {"action": "toggle_lock"})
        chat_response = self.client.post(url, {"action": "toggle_chat"})

        self.assertEqual(lock_response.status_code, 200)
        self.assertTrue(lock_response.json()["is_locked"])
        self.assertEqual(chat_response.status_code, 200)
        self.assertFalse(chat_response.json()["chat_enabled"])
        status_response = self.client.get(
            reverse("room_status", args=[self.session.room_name]),
        )
        self.assertEqual(status_response.status_code, 200)
        self.assertTrue(status_response.json()["is_locked"])
        self.assertFalse(status_response.json()["chat_enabled"])
        self.session.refresh_from_db()
        self.assertTrue(self.session.is_locked)
        self.assertFalse(self.session.chat_enabled)

    def test_mute_all_calls_livekit_and_updates_muted_participants(self):
        self.session.status = "live"
        self.session.save(update_fields=["status"])
        participant = SessionParticipant.objects.create(
            session=self.session,
            user=self.viewer,
        )
        self.client.force_login(self.user)

        with patch(
            "streaming.views.mute_all_livekit_participants",
            return_value={participant.livekit_identity},
        ) as mute_all:
            response = self.client.post(
                reverse("room_controls", args=[self.session.room_name]),
                {"action": "mute_all"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["muted_count"], 1)
        mute_all.assert_called_once_with(self.session)
        participant.refresh_from_db()
        self.assertTrue(participant.is_muted)


class VideoUploadWorkflowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.owner = user_model.objects.create_user(
            username="video-owner",
            email="video-owner@example.com",
            password="test-password",
        )
        self.viewer = user_model.objects.create_user(
            username="video-viewer",
            email="video-viewer@example.com",
            password="test-password",
        )
        self.client.force_login(self.owner)
        self.folder = f"radevlive/videos/user_{self.owner.pk}"
        self.public_id = f"{self.folder}/{uuid.uuid4().hex}"

    @override_settings(
        CLOUDINARY_CLOUD_NAME="demo",
        CLOUDINARY_API_KEY="test-key",
        CLOUDINARY_API_SECRET="test-secret",
    )
    def test_upload_page_configures_one_gigabyte_chunked_video_uploads(self):
        response = self.client.get(reverse("video_upload"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'type="file"')
        self.assertContains(response, "X-Unique-Upload-Id")
        self.assertNotContains(response, "upload-widget.cloudinary.com")

    @override_settings(
        CLOUDINARY_CLOUD_NAME="demo",
        CLOUDINARY_API_KEY="test-key",
        CLOUDINARY_API_SECRET="test-secret",
    )
    def test_upload_signature_creates_an_owner_scoped_upload_id(self):
        with patch(
            "streaming.video_views.cloudinary.utils.api_sign_request",
            return_value="test-signature",
        ) as sign_request:
            response = self.client.post(
                reverse("video_upload_signature"),
                data=json.dumps({}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["signature"], "test-signature")
        self.assertTrue(result["public_id"].startswith(self.folder + "/"))
        self.assertTrue(result["timestamp"])
        sign_request.assert_called_once_with(
            {
                "public_id": result["public_id"],
                "timestamp": result["timestamp"],
            },
            "test-secret",
        )

    @override_settings(
        CLOUDINARY_CLOUD_NAME="demo",
        CLOUDINARY_API_KEY="test-key",
        CLOUDINARY_API_SECRET="test-secret",
    )
    def test_upload_signature_rejects_another_users_id(self):
        public_id = "radevlive/videos/user_999/" + uuid.uuid4().hex
        with patch(
            "streaming.video_views.cloudinary.utils.api_sign_request",
        ) as sign_request:
            response = self.client.post(
                reverse("video_upload_signature"),
                data=json.dumps({"public_id": public_id}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 400)
        sign_request.assert_not_called()

    @override_settings(
        CLOUDINARY_CLOUD_NAME="demo",
        CLOUDINARY_API_KEY="test-key",
        CLOUDINARY_API_SECRET="test-secret",
    )
    def test_publish_saves_verified_cloudinary_asset_metadata(self):
        asset = {
            "bytes": 1024,
            "duration": 12.5,
            "format": "mp4",
            "public_id": self.public_id,
            "resource_type": "video",
            "secure_url": "https://res.cloudinary.com/demo/video/upload/asset.mp4",
            "type": "upload",
        }
        with patch(
            "streaming.video_views.cloudinary.api.resource",
            return_value=asset,
        ) as resource:
            response = self.client.post(
                reverse("video_upload"),
                {
                    "title": "Community video",
                    "description": "A verified upload",
                    "public_id": self.public_id,
                },
            )

        self.assertEqual(response.status_code, 302)
        resource.assert_called_once_with(
            self.public_id,
            resource_type="video",
            type="upload",
        )
        video = UploadedVideo.objects.get(cloudinary_public_id=self.public_id)
        self.assertEqual(video.owner, self.owner)
        self.assertEqual(video.size_bytes, 1024)
        self.assertEqual(video.duration_seconds, 12.5)
        self.assertEqual(video.format, "mp4")
        self.assertIn(",so_0.5,", video.thumbnail_url)
        self.assertIn("res.cloudinary.com/demo/video/upload", video.thumbnail_url)

    @override_settings(
        CLOUDINARY_CLOUD_NAME="demo",
        CLOUDINARY_API_KEY="test-key",
        CLOUDINARY_API_SECRET="test-secret",
    )
    def test_publish_rejects_cloudinary_assets_over_one_gigabyte(self):
        asset = {
            "bytes": 1024 * 1024 * 1024 + 1,
            "format": "mp4",
            "public_id": self.public_id,
            "resource_type": "video",
            "secure_url": "https://res.cloudinary.com/demo/video/upload/asset.mp4",
            "type": "upload",
        }
        with patch(
            "streaming.video_views.cloudinary.api.resource",
            return_value=asset,
        ):
            response = self.client.post(
                reverse("video_upload"),
                {
                    "title": "Too large",
                    "public_id": self.public_id,
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(UploadedVideo.objects.exists())
        self.assertContains(
            response,
            "Choose an MP4 or WebM video up to 1 GB.",
            status_code=400,
        )

    def test_public_library_and_creator_profile_display_uploaded_videos(self):
        profile, _ = UserProfile.objects.get_or_create(user=self.owner)
        video = UploadedVideo.objects.create(
            owner=self.owner,
            title="A public tutorial",
            description="A description",
            cloudinary_public_id=self.public_id,
            video_url="https://res.cloudinary.com/demo/video/upload/tutorial.mp4",
            thumbnail_url="https://res.cloudinary.com/demo/video/upload/tutorial.jpg",
            format="mp4",
            size_bytes=1024,
        )

        library_response = self.client.get(reverse("video_library"))
        self.client.force_login(self.viewer)
        profile_response = self.client.get(
            reverse(
                "user_profile",
                kwargs={"username": profile.username},
            ) + "?tab=videos",
        )

        self.assertContains(library_response, video.title)
        self.assertContains(profile_response, video.title)
        self.assertContains(
            library_response,
            reverse("video_detail", args=[video.pk]),
        )

    def test_video_detail_increments_view_count(self):
        video = UploadedVideo.objects.create(
            owner=self.owner,
            title="Counted video",
            cloudinary_public_id=self.public_id,
            video_url="https://res.cloudinary.com/demo/video/upload/video.mp4",
            thumbnail_url="https://res.cloudinary.com/demo/video/upload/video.jpg",
            format="mp4",
            size_bytes=1024,
        )

        response = self.client.get(reverse("video_detail", args=[video.pk]))

        self.assertEqual(response.status_code, 200)
        video.refresh_from_db()
        self.assertEqual(video.view_count, 1)
        self.assertContains(response, "1 views")

    def test_video_library_paginates_and_filters_by_creator(self):
        profile, _ = UserProfile.objects.get_or_create(user=self.owner)
        other_user = get_user_model().objects.create_user(
            username="another-video-owner",
            password="test-password",
        )
        for index in range(25):
            UploadedVideo.objects.create(
                owner=self.owner,
                title=f"Creator video {index}",
                cloudinary_public_id=f"{self.folder}/{uuid.uuid4().hex}",
                video_url="https://res.cloudinary.com/demo/video/upload/video.mp4",
                thumbnail_url="https://res.cloudinary.com/demo/video/upload/video.jpg",
                format="mp4",
                size_bytes=1024,
            )
        UploadedVideo.objects.create(
            owner=other_user,
            title="Different creator video",
            cloudinary_public_id=f"radevlive/videos/user_{other_user.pk}/{uuid.uuid4().hex}",
            video_url="https://res.cloudinary.com/demo/video/upload/other.mp4",
            thumbnail_url="https://res.cloudinary.com/demo/video/upload/other.jpg",
            format="mp4",
            size_bytes=1024,
        )

        response = self.client.get(
            reverse("video_library"),
            {"creator": profile.username, "page": 2},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Page 2 of 2")
        self.assertEqual(response.context["videos"].paginator.count, 25)
        self.assertEqual(len(response.context["videos"]), 1)
        self.assertNotContains(response, "Different creator video")

    def test_profile_links_to_all_videos_when_creator_has_more_than_twelve(self):
        profile, _ = UserProfile.objects.get_or_create(user=self.owner)
        for index in range(13):
            UploadedVideo.objects.create(
                owner=self.owner,
                title=f"Profile video {index}",
                cloudinary_public_id=f"{self.folder}/{uuid.uuid4().hex}",
                video_url="https://res.cloudinary.com/demo/video/upload/video.mp4",
                thumbnail_url="https://res.cloudinary.com/demo/video/upload/video.jpg",
                format="mp4",
                size_bytes=1024,
            )
        self.client.force_login(self.viewer)

        response = self.client.get(
            reverse("user_profile", kwargs={"username": profile.username}),
            {"tab": "videos"},
        )

        self.assertContains(response, "View all 13")
        self.assertContains(
            response,
            f"?creator={profile.username}",
        )

    @override_settings(
        CLOUDINARY_CLOUD_NAME="demo",
        CLOUDINARY_API_KEY="test-key",
        CLOUDINARY_API_SECRET="test-secret",
    )
    def test_owner_can_delete_cloudinary_video(self):
        video = UploadedVideo.objects.create(
            owner=self.owner,
            title="A video to delete",
            cloudinary_public_id=self.public_id,
            video_url="https://res.cloudinary.com/demo/video/upload/video.mp4",
            thumbnail_url="https://res.cloudinary.com/demo/video/upload/video.jpg",
            format="mp4",
            size_bytes=1024,
        )
        with patch(
            "streaming.video_views.cloudinary.uploader.destroy",
            return_value={"result": "ok"},
        ) as destroy:
            response = self.client.post(
                reverse("delete_video", args=[video.pk]),
            )

        self.assertRedirects(response, reverse("video_library"))
        destroy.assert_called_once_with(
            self.public_id,
            resource_type="video",
            type="upload",
            invalidate=True,
        )
        self.assertFalse(UploadedVideo.objects.filter(pk=video.pk).exists())


@override_settings(
    MPESA_API_BASE_URL="https://sandbox.safaricom.co.ke",
    MPESA_CONSUMER_KEY="test-consumer-key",
    MPESA_CONSUMER_SECRET="test-consumer-secret",
    MPESA_SHORTCODE="174379",
    MPESA_PASSKEY="test-passkey",
    MPESA_TRANSACTION_TYPE="CustomerPayBillOnline",
    MPESA_CALLBACK_BASE_URL="https://radev.example",
    MPESA_CALLBACK_TOKEN="test-callback-token",
    MPESA_TIP_MIN_AMOUNT=10,
    MPESA_TIP_MAX_AMOUNT=150000,
    MPESA_TIP_PRESETS=(50, 100, 500),
)
class MpesaTipWorkflowTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.creator = user_model.objects.create_user(
            username="tip-creator",
            password="test-password",
        )
        self.viewer = user_model.objects.create_user(
            username="tip-viewer",
            password="test-password",
        )
        self.video = UploadedVideo.objects.create(
            owner=self.creator,
            title="A video to tip",
            cloudinary_public_id=f"radevlive/videos/user_{self.creator.pk}/{uuid.uuid4().hex}",
            video_url="https://res.cloudinary.com/demo/video/upload/tip.mp4",
            thumbnail_url="https://res.cloudinary.com/demo/video/upload/tip.jpg",
            format="mp4",
            size_bytes=1024,
        )
        self.session = LiveSession.objects.create(
            host=self.creator,
            title="A live session to tip",
            status="live",
        )
        self.tip_data = {
            "target_type": "video",
            "target_id": self.video.pk,
            "amount": "100",
            "phone_number": "0712 345 678",
        }

    def _create_tip(self, **kwargs):
        values = {
            "sender": self.viewer,
            "recipient": self.creator,
            "recipient_name": "Tip Creator",
            "target_type": "video",
            "target_id": self.video.pk,
            "target_title": self.video.title,
            "amount": 100,
            "merchant_request_id": "merchant-request-1",
            "checkout_request_id": "checkout-request-1",
        }
        values.update(kwargs)
        return MpesaTip.objects.create(**values)

    def _callback(self, tip, *, result_code=0, amount=100):
        return {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": tip.merchant_request_id,
                    "CheckoutRequestID": tip.checkout_request_id,
                    "ResultCode": result_code,
                    "ResultDesc": "The service request is processed successfully.",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": amount},
                            {"Name": "MpesaReceiptNumber", "Value": "QXY123ABC"},
                        ],
                    },
                },
            },
        }

    def test_video_tip_starts_stk_and_normalizes_payer_phone(self):
        self.client.force_login(self.viewer)
        with patch(
            "streaming.tip_views.initiate_stk_push",
            return_value={
                "merchant_request_id": "merchant-1",
                "checkout_request_id": "checkout-1",
                "customer_message": "Prompt sent",
            },
        ) as initiate:
            response = self.client.post(
                reverse("mpesa_tip_create"),
                self.tip_data,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pending")
        tip = MpesaTip.objects.get(checkout_request_id="checkout-1")
        self.assertEqual(tip.sender, self.viewer)
        self.assertEqual(tip.recipient, self.creator)
        self.assertEqual(tip.amount, 100)
        initiate.assert_called_once_with(tip, "254712345678")

    def test_tip_rejects_self_tips_and_amounts_above_limit(self):
        self.client.force_login(self.creator)
        with patch("streaming.tip_views.initiate_stk_push") as initiate:
            self_tip_response = self.client.post(
                reverse("mpesa_tip_create"),
                self.tip_data,
            )
            self.client.force_login(self.viewer)
            over_limit_response = self.client.post(
                reverse("mpesa_tip_create"),
                {
                    **self.tip_data,
                    "target_id": self.video.pk,
                    "amount": "150001",
                },
            )

        self.assertEqual(self_tip_response.status_code, 400)
        self.assertEqual(over_limit_response.status_code, 400)
        self.assertFalse(MpesaTip.objects.exists())
        initiate.assert_not_called()

    def test_tip_rejects_invalid_phone_number(self):
        self.client.force_login(self.viewer)
        with patch("streaming.tip_views.initiate_stk_push") as initiate:
            response = self.client.post(
                reverse("mpesa_tip_create"),
                {**self.tip_data, "phone_number": "not-a-phone"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(MpesaTip.objects.exists())
        initiate.assert_not_called()

    def test_live_session_tip_targets_only_live_hosts(self):
        self.client.force_login(self.viewer)
        data = {
            **self.tip_data,
            "target_type": "live_session",
            "target_id": self.session.pk,
        }
        with patch(
            "streaming.tip_views.initiate_stk_push",
            return_value={
                "merchant_request_id": "merchant-live",
                "checkout_request_id": "checkout-live",
                "customer_message": "",
            },
        ) as initiate:
            response = self.client.post(reverse("mpesa_tip_create"), data)

        self.assertEqual(response.status_code, 200)
        tip = MpesaTip.objects.get(checkout_request_id="checkout-live")
        self.assertEqual(tip.target_type, "live_session")
        self.assertEqual(tip.recipient, self.creator)
        initiate.assert_called_once()

        self.session.status = "ended"
        self.session.save(update_fields=["status"])
        ended_response = self.client.post(reverse("mpesa_tip_create"), data)
        self.assertEqual(ended_response.status_code, 409)

    def test_stk_push_uses_configured_shortcode_and_callback(self):
        from . import mpesa

        tip = self._create_tip()

        class FakeResponse:
            ok = True

            def __init__(self, value):
                self.value = value

            def json(self):
                return self.value

        with (
            patch(
                "streaming.mpesa.requests.get",
                return_value=FakeResponse({"access_token": "test-token"}),
            ),
            patch(
                "streaming.mpesa.requests.post",
                return_value=FakeResponse({
                    "ResponseCode": "0",
                    "MerchantRequestID": "merchant-2",
                    "CheckoutRequestID": "checkout-2",
                }),
            ) as post,
        ):
            result = mpesa.initiate_stk_push(tip, "254712345678")

        self.assertEqual(result["checkout_request_id"], "checkout-2")
        args, kwargs = post.call_args
        self.assertEqual(
            args[0],
            "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
        )
        self.assertEqual(kwargs["json"]["BusinessShortCode"], "174379")
        self.assertEqual(kwargs["json"]["Amount"], 100)
        self.assertEqual(kwargs["json"]["PartyA"], "254712345678")
        self.assertEqual(
            kwargs["json"]["CallBackURL"],
            "https://radev.example/payments/mpesa/callback/test-callback-token/",
        )

    def test_callback_verifies_payment_and_is_idempotent(self):
        tip = self._create_tip()
        url = reverse(
            "mpesa_tip_callback",
            kwargs={"token": "test-callback-token"},
        )
        with patch(
            "streaming.tip_views.query_stk_push",
            return_value={"ResultCode": "0"},
        ) as query:
            response = self.client.post(
                url,
                data=json.dumps(self._callback(tip)),
                content_type="application/json",
            )
            duplicate_response = self.client.post(
                url,
                data=json.dumps(self._callback(tip)),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(duplicate_response.status_code, 200)
        query.assert_called_once_with("checkout-request-1")
        tip.refresh_from_db()
        self.assertEqual(tip.status, "succeeded")
        self.assertEqual(tip.payout_status, "unpaid")
        self.assertEqual(tip.mpesa_receipt_number, "QXY123ABC")

    def test_callback_does_not_mark_an_unverified_payment_as_successful(self):
        tip = self._create_tip()
        with patch(
            "streaming.tip_views.query_stk_push",
            return_value={"ResultCode": "1"},
        ):
            response = self.client.post(
                reverse(
                    "mpesa_tip_callback",
                    kwargs={"token": "test-callback-token"},
                ),
                data=json.dumps(self._callback(tip)),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 503)
        tip.refresh_from_db()
        self.assertEqual(tip.status, "pending")
        self.assertIsNone(tip.mpesa_receipt_number)

    def test_tip_forms_are_available_for_videos_and_live_sessions(self):
        self.client.force_login(self.viewer)
        video_response = self.client.get(
            reverse("video_detail", args=[self.video.pk]),
        )
        session_response = self.client.get(
            reverse("session_detail", args=[self.session.room_name]),
        )

        self.assertContains(video_response, "Tip tip-creator")
        self.assertContains(video_response, 'value="video"')
        self.assertContains(session_response, "Tip tip-creator")
        self.assertContains(session_response, 'value="live_session"')
        self.assertContains(
            video_response,
            "creator payouts are handled manually",
        )
