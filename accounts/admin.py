
from django.contrib import admin
from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'username',
        'is_host',
        'is_verified',
        'created_at',
    )

    search_fields = ('user__email', 'username', 'full_name')
    list_filter = ('is_host', 'is_verified')
