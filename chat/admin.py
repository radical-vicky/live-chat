from django.contrib import admin
from .models import ChatMessage


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("session", "user", "message", "created_at")
    list_filter = ("created_at",)
    search_fields = (
        "session__title",
        "user__email",
        "message",
    )
    readonly_fields = ("created_at",)