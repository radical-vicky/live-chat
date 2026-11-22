import logging

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.views.decorators.http import require_POST
from django.utils import timezone
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from streaming.models import LiveSession, UploadedVideo
from streaming.services import (
    PreviewRecordingError,
    delete_preview_recording,
    refresh_preview_recording,
    stop_preview_recording,
)

User = get_user_model()
logger = logging.getLogger(__name__)

def home(request):
    # 1. Fetch Live Sessions for the grid
    live_sessions = (
        LiveSession.objects
        .filter(visibility="public", status="live")
        .select_related("host")
        .order_by("-started_at")[:4]
    )

    # 2. Fetch Upcoming Sessions
    upcoming_sessions = (
        LiveSession.objects
        .filter(visibility="public", status="scheduled")
        .select_related("host")
        .order_by("scheduled_at")[:3]
    )

    # 3. Dynamic Stats for the landing page
    live_count = LiveSession.objects.filter(status="live").count()
    total_hosts = User.objects.filter(profile__is_host=True).count()
    total_sessions = LiveSession.objects.filter(status="ended").count()
    videos = UploadedVideo.objects.select_related(
        "owner",
        "owner__profile",
    ).order_by("-created_at")[:6]

    return render(request, "core/index.html", {
        "live_sessions": live_sessions,
        "upcoming_sessions": upcoming_sessions,
        "videos": videos,
        "live_count": live_count,
        "total_hosts": total_hosts,
        "total_sessions": total_sessions,
    })

@login_required
def dashboard(request):
    user = request.user
    base = LiveSession.objects.filter(host=user).select_related("host")

    my_live = base.filter(status="live").order_by("-started_at")[:5]
    my_scheduled = base.filter(status="scheduled").order_by("scheduled_at")[:5]
    my_ended = base.filter(status="ended").order_by("-ended_at")[:5]

    trending = LiveSession.objects.filter(
        visibility="public",
        status__in=["live", "scheduled"],
    ).select_related("host").exclude(host=user)[:6]
    live_now = LiveSession.objects.filter(
        visibility="public",
        status="live",
    ).select_related("host").order_by("-started_at")[:3]
    videos = UploadedVideo.objects.select_related(
        "owner",
        "owner__profile",
    ).order_by("-created_at")[:6]

    return render(request, "core/dashboard.html", {
        "my_live": my_live,
        "my_scheduled": my_scheduled,
        "my_ended": my_ended,
        "trending": trending,
        "live_now": live_now,
        "videos": videos,
    })

def about(request):
    return render(request, "core/about.html")


@login_required
def admin_overview(request):
    if not request.user.is_staff:
        return redirect("core:dashboard")
    users = User.objects.select_related("profile").all()[:12]
    stats = {
        "users": User.objects.count(),
        "live": LiveSession.objects.filter(status="live").count(),
        "sessions": LiveSession.objects.count(),
    }
    return render(request, "core/admin_overview.html", {
        "users": users,
        "stats": stats,
    })


@login_required
def admin_sessions(request):
    if not request.user.is_staff:
        return redirect("core:dashboard")
    sessions = LiveSession.objects.select_related("host").order_by("-created_at")
    return render(request, "core/admin_sessions.html", {"sessions": sessions})


@login_required
@require_POST
def admin_end_session(request, room_name):
    if not request.user.is_staff:
        return HttpResponseForbidden("Administrator access required.")
    session = LiveSession.objects.filter(room_name=room_name).first()
    if session is None:
        messages.error(request, "Session not found.")
        return redirect("core:admin_sessions")
    if session.status != "ended":
        recording_finished = True
        if session.preview_status == "recording":
            try:
                stop_preview_recording(session)
            except PreviewRecordingError:
                logger.exception(
                    "Administrator could not finish replay recording for session=%s",
                    session.room_name,
                )
                session.preview_status = "failed"
                session.save(update_fields=["preview_status"])
                recording_finished = False
        session.end()
        session.participants.filter(left_at__isnull=True).update(
            left_at=timezone.now()
        )
        async_to_sync(get_channel_layer().group_send)(
            f"chat_{session.room_name.hex}",
            {"type": "session_ended"},
        )
    else:
        recording_finished = True
    if recording_finished:
        messages.success(request, "Session ended.")
    else:
        messages.error(request, "Session ended, but its replay could not be saved.")
    return redirect("core:admin_sessions")


@login_required
@require_POST
def admin_remove_session(request, room_name):
    if not request.user.is_staff:
        return HttpResponseForbidden("Administrator access required.")
    session = LiveSession.objects.filter(room_name=room_name).first()
    if session is None:
        messages.error(request, "Session not found.")
        return redirect("core:admin_sessions")
    if session.preview_status == "recording":
        try:
            stop_preview_recording(session)
        except PreviewRecordingError:
            logger.exception(
                "Administrator could not finish replay recording for session=%s",
                session.room_name,
            )
            messages.error(
                request,
                "The session could not be removed because its replay recording could not be stopped.",
            )
            return redirect("core:admin_sessions")
    if session.preview_status == "processing":
        try:
            refresh_preview_recording(session)
        except PreviewRecordingError:
            logger.exception(
                "Administrator could not check replay state for session=%s",
                session.room_name,
            )
            messages.error(
                request,
                "The session could not be removed because its replay status could not be checked.",
            )
            return redirect("core:admin_sessions")
        if session.preview_status == "processing":
            messages.error(
                request,
                "Wait for the replay to finish processing before removing this session.",
            )
            return redirect("core:admin_sessions")
    if session.preview_object_key:
        try:
            delete_preview_recording(session)
        except PreviewRecordingError:
            logger.exception(
                "Administrator could not delete replay for session=%s",
                session.room_name,
            )
            messages.error(
                request,
                "The session could not be removed because its replay could not be deleted.",
            )
            return redirect("core:admin_sessions")
    if session.status != "ended":
        session.end()
        session.participants.filter(left_at__isnull=True).update(
            left_at=timezone.now()
        )
        async_to_sync(get_channel_layer().group_send)(
            f"chat_{session.room_name.hex}",
            {"type": "session_ended"},
        )
    session.delete()
    messages.success(request, "Session removed.")
    return redirect("core:admin_sessions")