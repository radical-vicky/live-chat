from django.contrib import admin
from django.utils import timezone

from .models import LiveSession, MpesaTip, SessionParticipant


class SessionParticipantInline(admin.TabularInline):
    model = SessionParticipant
    extra = 0
    readonly_fields = ("joined_at", "left_at")


@admin.register(LiveSession)
class LiveSessionAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "host",
        "visibility",
        "status",
        "scheduled_at",
        "created_at",
    )

    list_filter = ("visibility", "status", "created_at")
    search_fields = ("title", "host__email")
    readonly_fields = (
        "room_name",
        "created_at",
        "started_at",
        "ended_at",
    )
    inlines = [SessionParticipantInline]


@admin.register(SessionParticipant)
class SessionParticipantAdmin(admin.ModelAdmin):
    list_display = ("session", "user", "joined_at", "left_at")
    search_fields = ("session__title", "user__email")
    list_filter = ("joined_at", "left_at")


@admin.register(MpesaTip)
class MpesaTipAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "recipient_name",
        "target_type",
        "target_title",
        "amount",
        "status",
        "mpesa_receipt_number",
        "payout_status",
    )
    list_filter = ("status", "payout_status", "target_type", "created_at")
    search_fields = (
        "recipient_name",
        "recipient__username",
        "sender__username",
        "target_title",
        "mpesa_receipt_number",
    )
    readonly_fields = (
        "public_id",
        "sender",
        "recipient",
        "recipient_name",
        "target_type",
        "target_id",
        "target_title",
        "amount",
        "currency",
        "status",
        "merchant_request_id",
        "checkout_request_id",
        "mpesa_receipt_number",
        "failure_reason",
        "payout_status",
        "created_at",
        "paid_at",
        "payout_paid_at",
    )
    actions = ("mark_creator_payout_sent",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description="Mark successful tips as manually paid to creators")
    def mark_creator_payout_sent(self, request, queryset):
        updated = queryset.filter(
            status="succeeded",
            payout_status="unpaid",
        ).update(
            payout_status="paid",
            payout_paid_at=timezone.now(),
        )
        self.message_user(
            request,
            f"{updated} creator payout(s) marked as paid.",
        )