from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from streaming.models import CreatorFollow, LiveSession

from .models import UserProfile


class ProfilePageTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="profile-owner",
            email="owner@example.com",
            password="test-password",
        )
        self.follower = user_model.objects.create_user(
            username="profile-follower",
            email="follower@example.com",
            password="test-password",
        )
        self.following = user_model.objects.create_user(
            username="profile-following",
            email="following@example.com",
            password="test-password",
        )
        self.profile, _ = UserProfile.objects.get_or_create(
            user=self.user,
            defaults={"full_name": "Profile Owner"},
        )
        self.profile.full_name = "Profile Owner"
        self.profile.save()
        self.session = LiveSession.objects.create(
            host=self.user,
            title="A real upcoming session",
            status="scheduled",
        )
        CreatorFollow.objects.create(
            follower=self.follower,
            following=self.user,
        )
        CreatorFollow.objects.create(
            follower=self.user,
            following=self.following,
        )
        self.client.force_login(self.user)

    def test_profile_displays_real_session_and_relationship_counts(self):
        response = self.client.get(reverse("profile"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Profile Owner")
        self.assertContains(response, "A real upcoming session")
        self.assertEqual(response.context["followers_count"], 1)
        self.assertEqual(response.context["following_count"], 1)
        self.assertEqual(response.context["session_count"], 1)

    def test_user_profile_is_read_only_and_shows_follow_action(self):
        public_profile, _ = UserProfile.objects.get_or_create(
            user=self.follower,
        )
        public_profile.full_name = "Follower Profile"
        public_profile.bio = "A public profile bio"
        public_profile.save()
        profile_url = reverse(
            "user_profile",
            kwargs={"username": public_profile.username},
        )

        response = self.client.get(profile_url + "?tab=about")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Follower Profile")
        self.assertContains(response, "A public profile bio")
        self.assertContains(response, "Follow")
        self.assertNotContains(response, self.follower.email)
        self.assertNotContains(response, "Edit profile")
        self.assertNotContains(response, "Save profile")

    def test_user_profile_redirects_owner_to_editable_profile(self):
        response = self.client.get(
            reverse(
                "user_profile",
                kwargs={"username": self.profile.username},
            ),
        )

        self.assertRedirects(response, reverse("profile"))

    def test_profile_settings_save_updates_profile_and_account_name(self):
        response = self.client.post(
            reverse("profile") + "?tab=settings",
            {
                "full_name": "Updated Name",
                "username": self.profile.username,
                "bio": "A new bio",
            },
        )

        self.assertRedirects(
            response,
            reverse("profile") + "?tab=settings",
        )
        self.profile.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.profile.full_name, "Updated Name")
        self.assertEqual(self.profile.bio, "A new bio")
        self.assertEqual(self.user.first_name, "Updated Name")
