
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class LiveSession(models.Model):
    CATEGORY_CHOICES = [
        ("education", "Education"),
        ("business", "Business"),
        ("entertainment", "Entertainment"),
        ("gaming", "Gaming"),
        ("technology", "Technology"),
        ("music", "Music"),
        ("fitness", "Fitness"),
        ("cooking", "Cooking"),
    ]
    VISIBILITY_CHOICES = [
        ("public", "Public"),
        ("private", "Private"),
    ]

    STATUS_CHOICES = [
        ("scheduled", "Not started"),
        ("live", "Live"),
        ("ended", "Ended"),
    ]
    PREVIEW_STATUS_CHOICES = [
        ("none", "No preview"),
        ("recording", "Recording"),
        ("processing", "Processing"),
        ("ready", "Ready"),
        ("failed", "Failed"),
        ("deleted", "Deleted"),
    ]

    host = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hosted_sessions",
    )
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default="education",
    )
    room_name = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
    )
    visibility = models.CharField(
        max_length=10,
        choices=VISIBILITY_CHOICES,
        default="public",
    )
    status = models.CharField(
        max_length=15,
        choices=STATUS_CHOICES,
        default="scheduled",
    )
    is_locked = models.BooleanField(default=False)
    chat_enabled = models.BooleanField(default=True)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    participant_limit = models.PositiveIntegerField(default=50)
    thumbnail = models.ImageField(
        upload_to="stream_thumbnails/",
        null=True,
        blank=True,
    )
    preview_status = models.CharField(
        max_length=12,
        choices=PREVIEW_STATUS_CHOICES,
        default="none",
    )
    preview_egress_id = models.CharField(max_length=100, blank=True)
    preview_object_key = models.CharField(max_length=500, blank=True)
    host_recording_consented_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def active_participant_count(self):
        return self.participants.filter(left_at__isnull=True).count()

    def start(self):
        self.status = "live"
        self.started_at = timezone.now()
        self.ended_at = None
        self.save(
            update_fields=["status", "started_at", "ended_at"]
        )

    def end(self):
        self.status = "ended"
        self.ended_at = timezone.now()
        self.save(update_fields=["status", "ended_at"])


class UploadedVideo(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="uploaded_videos",
    )
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    cloudinary_public_id = models.CharField(max_length=255, unique=True)
    video_url = models.URLField(max_length=1000)
    thumbnail_url = models.URLField(max_length=1000)
    format = models.CharField(max_length=20)
    size_bytes = models.PositiveBigIntegerField()
    duration_seconds = models.FloatField(null=True, blank=True)
    view_count = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class MpesaTip(models.Model):
    TARGET_CHOICES = [
        ("video", "Video"),
        ("live_session", "Live session"),
    ]
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("succeeded", "Succeeded"),
        ("failed", "Failed"),
    ]
    PAYOUT_STATUS_CHOICES = [
        ("not_ready", "Not ready"),
        ("unpaid", "Unpaid"),
        ("paid", "Paid manually"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="sent_mpesa_tips",
        null=True,
        blank=True,
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="received_mpesa_tips",
        null=True,
        blank=True,
    )
    recipient_name = models.CharField(max_length=150)
    target_type = models.CharField(max_length=12, choices=TARGET_CHOICES)
    target_id = models.PositiveBigIntegerField()
    target_title = models.CharField(max_length=150)
    amount = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="KES")
    status = models.CharField(
        max_length=12,
        choices=STATUS_CHOICES,
        default="pending",
    )
    payout_status = models.CharField(
        max_length=12,
        choices=PAYOUT_STATUS_CHOICES,
        default="not_ready",
    )
    merchant_request_id = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
    )
    checkout_request_id = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
    )
    mpesa_receipt_number = models.CharField(
        max_length=30,
        unique=True,
        null=True,
        blank=True,
    )
    failure_reason = models.CharField(max_length=255, blank=True)
    payout_reference = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    payout_paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["recipient", "status"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gte=10),
                name="mpesa_tip_amount_minimum",
            ),
        ]

    def __str__(self):
        return f"{self.currency} {self.amount} tip to {self.recipient_name}"


class SessionParticipant(models.Model):
    session = models.ForeignKey(
        LiveSession,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="session_participations",
    )
    livekit_identity = models.CharField(
        max_length=100,
        unique=True,
        null=True,
        blank=True,
        editable=False,
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    is_muted = models.BooleanField(default=False)
    recording_consented_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["joined_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "user"],
                name="unique_user_per_session",
            )
        ]

    def save(self, *args, **kwargs):
        if not self.livekit_identity:
            self.livekit_identity = (
                f"user-{self.user_id}-{uuid.uuid4().hex[:12]}"
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user} - {self.session.title}"


class FavoriteSession(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="favorite_sessions",
    )
    session = models.ForeignKey(
        LiveSession,
        on_delete=models.CASCADE,
        related_name="favorited_by",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "session"],
                name="unique_favorite_session",
            )
        ]
        ordering = ["-created_at"]


class PreviewLike(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="preview_likes",
    )
    session = models.ForeignKey(
        LiveSession,
        on_delete=models.CASCADE,
        related_name="preview_likes",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "session"],
                name="unique_preview_like",
            )
        ]
        ordering = ["-created_at"]


class PreviewComment(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="preview_comments",
    )
    session = models.ForeignKey(
        LiveSession,
        on_delete=models.CASCADE,
        related_name="preview_comments",
    )
    text = models.CharField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class SessionReminder(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="session_reminders",
    )
    session = models.ForeignKey(
        LiveSession,
        on_delete=models.CASCADE,
        related_name="reminders",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "session"],
                name="unique_session_reminder",
            )
        ]


class Notification(models.Model):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="live_notifications",
    )
    session = models.ForeignKey(
        LiveSession,
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
    )
    message = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]


class CreatorFollow(models.Model):
    follower = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="creator_follows",
    )
    following = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="followers",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["follower", "following"],
                name="unique_creator_follow",
            ),
            models.CheckConstraint(
                condition=~models.Q(follower=models.F("following")),
                name="creator_cannot_follow_self",
            ),
        ]
