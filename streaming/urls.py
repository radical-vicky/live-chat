from django.urls import path
from . import views

urlpatterns = [
    path("", views.explore_sessions, name="explore_sessions"),
    path("create/", views.create_session, name="create_session"),
    path("my-sessions/", views.my_sessions, name="my_sessions"),
    path("join/", views.join_meeting, name="join_meeting"),
    path("upcoming/", views.upcoming_sessions, name="upcoming_sessions"),
    path("favorites/", views.favorites, name="favorites"),
    path("messages/", views.messages, name="messages"),
    path("notifications/", views.notifications, name="notifications"),
    path(
        "notifications/<int:notification_id>/read/",
        views.mark_notification_read,
        name="mark_notification_read",
    ),
    path("favorite/<uuid:room_name>/", views.toggle_favorite, name="toggle_favorite"),
    path("reminder/<uuid:room_name>/", views.toggle_reminder, name="toggle_reminder"),
    path("follow/<int:user_id>/", views.toggle_follow, name="toggle_follow"),
    path(
        "<uuid:room_name>/preview/like/",
        views.toggle_preview_like,
        name="toggle_preview_like",
    ),
    path(
        "<uuid:room_name>/preview/comments/",
        views.add_preview_comment,
        name="add_preview_comment",
    ),
    path(
        "<uuid:room_name>/preview/edit/",
        views.edit_preview,
        name="edit_preview",
    ),
    path(
        "<uuid:room_name>/preview/delete/",
        views.delete_preview,
        name="delete_preview",
    ),
    path(
        "<uuid:room_name>/preview/status/",
        views.preview_recording_status,
        name="preview_recording_status",
    ),
    path(
        "<uuid:room_name>/preview/start/",
        views.start_preview_recording_view,
        name="start_preview_recording",
    ),
    path("creators/<slug:username>/", views.creator_profile, name="creator_profile"),

    path("<uuid:room_name>/", views.session_detail, name="session_detail"),
    path("<uuid:room_name>/start/", views.start_session, name="start_session"),
    path("<uuid:room_name>/end/", views.end_session, name="end_session"),
    path("<uuid:room_name>/join/", views.join_session, name="join_session"),
    path("<uuid:room_name>/token/", views.session_token, name="session_token"),
    path("<uuid:room_name>/leave/", views.leave_session, name="leave_session"),
    path(
    "<uuid:room_name>/status/",
    views.room_status,
    name="room_status",
),
path(
    "<uuid:room_name>/remove/<int:user_id>/",
    views.remove_participant,
    name="remove_participant",
),
path(
    "<uuid:room_name>/controls/",
    views.room_controls,
    name="room_controls",
),
path(
    "<uuid:room_name>/mute/<int:user_id>/",
    views.toggle_participant_mute,
    name="toggle_participant_mute",
),
path(
    "<uuid:room_name>/microphone/",
    views.update_microphone_status,
    name="update_microphone_status",
),
]