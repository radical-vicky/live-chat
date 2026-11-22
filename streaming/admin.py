from django.contrib import admin

from .models import LiveSession, SessionParticipant


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