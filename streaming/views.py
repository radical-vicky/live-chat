
import logging
import uuid

from django.conf import settings
from django.contrib import messages as django_messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import (
    get_object_or_404,
    redirect,
    render,
)
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from accounts.models import UserProfile
from .forms import LiveSessionForm, PreviewCommentForm, PreviewDetailsForm
from .models import (
    FavoriteSession,
    CreatorFollow,
    LiveSession,
    Notification,
    PreviewComment,
    PreviewLike,
    SessionParticipant,
    SessionReminder,
)
from .services import (
    PreviewRecordingError,
    check_preview_recording_settings,
    create_livekit_token,
    delete_preview_recording,
    mute_all_livekit_participants,
    remove_livekit_participant,
    preview_video_url,
    refresh_preview_recording,
    start_preview_recording,
    stop_preview_recording,
    set_livekit_participant_muted,
)


logger = logging.getLogger(__name__)


def _redirect_to_next(request, default_name):
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect(default_name)


def _redirect_to_session_or_next(request, session):
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)
    return redirect("session_detail", room_name=session.room_name)


def _finish_preview_recording(request, session):
    if session.preview_status != "recording":
        return True
    try:
        stop_preview_recording(session)
    except PreviewRecordingError:
        logger.exception(
            "Could not finish replay recording for session=%s",
            session.room_name,
        )
        session.preview_status = "failed"
        session.save(update_fields=["preview_status"])
        django_messages.error(
            request,
            "Your live session ended, but its replay could not be saved.",
        )
        return False
    return True


def _notify_live_session(session):
    recipient_ids = set(
        SessionReminder.objects.filter(session=session)
        .values_list("user_id", flat=True)
    )
    recipient_ids.update(
        FavoriteSession.objects.filter(session=session)
        .values_list("user_id", flat=True)
    )
    if session.visibility == "public":
        recipient_ids.update(
            CreatorFollow.objects.filter(following=session.host)
            .values_list("follower_id", flat=True)
        )
    recipient_ids.discard(session.host_id)
    channel_layer = get_channel_layer()
    for recipient_id in recipient_ids:
        notification = Notification.objects.create(
            recipient_id=recipient_id,
            session=session,
            message=f"{session.title} is live now.",
        )
        async_to_sync(channel_layer.group_send)(
            f"user_{recipient_id}",
            {
                "type": "notification",
                "payload": {
                    "id": notification.pk,
                    "message": notification.message,
                    "session_title": session.title,
                    "url": reverse(
                        "session_detail",
                        args=[session.room_name],
                    ),
                    "created_at": notification.created_at.isoformat(),
                },
            },
        )


def join_meeting(request):
    if request.method == "POST":
        code = request.POST.get("code", "").strip()
        if code:
            code = code.rstrip("/").rsplit("/", 1)[-1]
            try:
                room_name = uuid.UUID(code)
            except ValueError:
                django_messages.error(request, "Enter a valid session code or invite link.")
            else:
                session = get_object_or_404(LiveSession, room_name=room_name)
                return redirect("session_detail", room_name=session.room_name)
        else:
            django_messages.error(request, "Enter a session code or invite link.")
    return render(request, "streaming/join_meeting.html")


@login_required
def upcoming_sessions(request):
    sessions = LiveSession.objects.filter(
        visibility="public",
        status="scheduled",
    ).select_related("host").order_by("scheduled_at")
    reminders = set(
        SessionReminder.objects.filter(user=request.user)
        .values_list("session_id", flat=True)
    )
    return render(request, "streaming/upcoming.html", {
        "sessions": sessions,
        "reminder_session_ids": reminders,
    })


@login_required
def favorites(request):
    favorites = FavoriteSession.objects.filter(
        user=request.user,
        session__visibility="public",
    ).filter(
        Q(session__status__in=["live", "scheduled"])
        | Q(session__status="ended", session__preview_status="ready")
    ).select_related("session", "session__host")
    following = CreatorFollow.objects.filter(
        follower=request.user
    ).select_related("following", "following__profile")
    return render(request, "streaming/favorites.html", {
        "favorites": favorites,
        "following": following,
    })


@login_required
def messages(request):
    from chat.models import DirectMessage
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    contacts = user_model.objects.filter(is_active=True).exclude(
        pk=request.user.pk
    ).select_related("profile")
    selected_contact = None
    contact_id = request.GET.get("with")
    if contact_id:
        selected_contact = get_object_or_404(contacts, pk=contact_id)
    elif contacts.exists():
        selected_contact = contacts.first()

    if request.method == "POST":
        selected_contact = get_object_or_404(
            contacts, pk=request.POST.get("recipient")
        )
        text = request.POST.get("message", "").strip()
        if text and len(text) <= 1000:
            direct_message = DirectMessage.objects.create(
                sender=request.user,
                recipient=selected_contact,
                message=text,
            )
            notification = Notification.objects.create(
                recipient=selected_contact,
                message=f"New message from {request.user.get_username()}",
            )
            profile = getattr(request.user, "profile", None)
            username = (
                profile.full_name
                if profile and profile.full_name
                else request.user.get_username()
            )
            payload = {
                "id": direct_message.pk,
                "sender_id": request.user.pk,
                "username": username,
                "message": direct_message.message,
                "created_at": direct_message.created_at.isoformat(),
            }
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                "dm_%s_%s" % tuple(sorted((
                    request.user.pk,
                    selected_contact.pk,
                ))),
                {"type": "direct_message", "payload": payload},
            )
            async_to_sync(channel_layer.group_send)(
                f"user_{selected_contact.pk}",
                {
                    "type": "notification",
                    "payload": {
                        "id": notification.pk,
                        "message": notification.message,
                        "session_title": "",
                        "url": f"{reverse('messages')}?with={request.user.pk}",
                        "created_at": notification.created_at.isoformat(),
                    },
                },
            )
            django_messages.success(request, "Message sent.")
        else:
            django_messages.error(
                request,
                "Enter a message of 1 to 1000 characters.",
            )
        return redirect(f"{reverse('messages')}?with={selected_contact.pk}")

    thread = DirectMessage.objects.none()
    if selected_contact:
        thread = DirectMessage.objects.filter(
            Q(sender=request.user, recipient=selected_contact)
            | Q(sender=selected_contact, recipient=request.user)
        ).select_related("sender", "recipient")
    return render(request, "streaming/messages.html", {
        "contacts": contacts,
        "selected_contact": selected_contact,
        "thread": thread,
    })


@login_required
def notifications(request):
    notifications_list = Notification.objects.filter(
        recipient=request.user,
    ).select_related("session", "session__host")
    return render(request, "streaming/notifications.html", {
        "notifications": notifications_list,
        "unread_count": notifications_list.filter(read_at__isnull=True).count(),
    })


@login_required
@require_POST
def toggle_favorite(request, room_name):
    session = LiveSession.objects.filter(
        room_name=room_name,
        visibility="public",
    ).filter(
        Q(status__in=["live", "scheduled"])
        | Q(status="ended", preview_status="ready")
    ).first()
    if session is None:
        django_messages.warning(request, "That session is no longer available.")
        return _redirect_to_next(request, "favorites")

    favorite, created = FavoriteSession.objects.get_or_create(
        user=request.user,
        session=session,
    )
    if not created:
        favorite.delete()
    return _redirect_to_next(request, "favorites")


def _get_watchable_preview(room_name, user):
    session = get_object_or_404(
        LiveSession,
        room_name=room_name,
        status="ended",
        preview_status="ready",
    )
    if session.visibility == "private" and session.host_id != user.id:
        raise PermissionDenied(
            "You do not have permission to view this private preview."
        )
    return session


@login_required
@require_POST
def toggle_preview_like(request, room_name):
    session = _get_watchable_preview(room_name, request.user)
    like, created = PreviewLike.objects.get_or_create(
        user=request.user,
        session=session,
    )
    if not created:
        like.delete()
    return _redirect_to_session_or_next(request, session)


@login_required
@require_POST
def add_preview_comment(request, room_name):
    session = _get_watchable_preview(room_name, request.user)
    form = PreviewCommentForm(request.POST)
    if form.is_valid():
        PreviewComment.objects.create(
            user=request.user,
            session=session,
            text=form.cleaned_data["text"],
        )
        django_messages.success(request, "Your comment was added.")
    else:
        django_messages.error(request, "Enter a comment of up to 1000 characters.")
    return _redirect_to_session_or_next(request, session)


@login_required
@require_http_methods(["GET", "POST"])
def edit_preview(request, room_name):
    session = get_object_or_404(
        LiveSession,
        room_name=room_name,
        host=request.user,
        status="ended",
        preview_status="ready",
    )
    if request.method == "POST":
        form = PreviewDetailsForm(
            request.POST,
            request.FILES,
            instance=session,
        )
        if form.is_valid():
            form.save()
            django_messages.success(request, "Your preview details were updated.")
            return redirect("session_detail", room_name=session.room_name)
    else:
        form = PreviewDetailsForm(instance=session)
    return render(
        request,
        "streaming/edit_preview.html",
        {"session": session, "form": form},
    )


@login_required
@require_POST
def delete_preview(request, room_name):
    session = get_object_or_404(
        LiveSession,
        room_name=room_name,
        host=request.user,
        status="ended",
        preview_status="ready",
    )
    try:
        delete_preview_recording(session)
    except PreviewRecordingError:
        logger.exception(
            "Could not delete replay for session=%s",
            session.room_name,
        )
        django_messages.error(request, "The preview could not be deleted.")
        return redirect("session_detail", room_name=session.room_name)

    session.preview_status = "deleted"
    session.preview_egress_id = ""
    session.preview_object_key = ""
    session.save(update_fields=[
        "preview_status",
        "preview_egress_id",
        "preview_object_key",
    ])
    PreviewLike.objects.filter(session=session).delete()
    PreviewComment.objects.filter(session=session).delete()
    django_messages.success(request, "Your preview was deleted.")
    return redirect("session_detail", room_name=session.room_name)


@login_required
@require_POST
def start_preview_recording_view(request, room_name):
    session = get_object_or_404(
        LiveSession,
        room_name=room_name,
        host=request.user,
        status="live",
    )
    participant = get_object_or_404(
        SessionParticipant,
        session=session,
        user=request.user,
        left_at__isnull=True,
    )
    if (
        session.host_recording_consented_at is None
        or participant.recording_consented_at is None
    ):
        return JsonResponse(
            {"error": "Agree to the recording notice before going live."},
            status=403,
        )

    try:
        egress_id = start_preview_recording(session)
    except PreviewRecordingError as error:
        logger.exception(
            "Could not start replay recording for session=%s",
            session.room_name,
        )
        participant.left_at = timezone.now()
        participant.save(update_fields=["left_at"])
        return JsonResponse({"error": str(error)}, status=503)
    return JsonResponse({"status": session.preview_status, "egress_id": egress_id})


@login_required
@require_http_methods(["GET"])
def preview_recording_status(request, room_name):
    session = get_object_or_404(LiveSession, room_name=room_name)
    if session.visibility == "private" and session.host_id != request.user.id:
        raise PermissionDenied(
            "You do not have permission to view this private preview."
        )
    try:
        refresh_preview_recording(session)
        video_url = (
            preview_video_url(session)
            if session.preview_status == "ready"
            else ""
        )
    except PreviewRecordingError:
        logger.exception(
            "Could not refresh replay state for session=%s",
            session.room_name,
        )
        return JsonResponse(
            {"error": "The replay status could not be checked."},
            status=503,
        )
    return JsonResponse({
        "status": session.preview_status,
        "video_url": video_url,
    })


@login_required
@require_POST
def toggle_reminder(request, room_name):
    session = get_object_or_404(
        LiveSession,
        room_name=room_name,
        visibility="public",
        status="scheduled",
    )
    reminder, created = SessionReminder.objects.get_or_create(
        user=request.user,
        session=session,
    )
    if not created:
        reminder.delete()
    return _redirect_to_next(request, "upcoming_sessions")


@login_required
@require_POST
def toggle_follow(request, user_id):
    from django.contrib.auth import get_user_model

    target = get_object_or_404(
        get_user_model().objects.filter(is_active=True).exclude(
            pk=request.user.pk
        ),
        pk=user_id,
    )
    follow, created = CreatorFollow.objects.get_or_create(
        follower=request.user,
        following=target,
    )
    if not created:
        follow.delete()
    return _redirect_to_next(request, "favorites")


@login_required
@require_POST
def mark_notification_read(request, notification_id):
    notification = get_object_or_404(
        Notification,
        pk=notification_id,
        recipient=request.user,
    )
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
    return redirect("notifications")


def explore_sessions(request):
    sessions = LiveSession.objects.filter(
        visibility="public",
        status__in=["live", "scheduled"],
    ).select_related("host", "host__profile")
    previews = LiveSession.objects.filter(
        visibility="public",
        status="ended",
        preview_status="ready",
    ).select_related("host", "host__profile")
    query = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    valid_categories = {choice[0] for choice in LiveSession.CATEGORY_CHOICES}
    if category in valid_categories:
        sessions = sessions.filter(category=category)
        previews = previews.filter(category=category)
    if query:
        search_filter = (
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(host__email__icontains=query)
            | Q(host__profile__full_name__icontains=query)
        )
        sessions = sessions.filter(search_filter)
        previews = previews.filter(search_filter)

    return render(
        request,
        "streaming/explore.html",
        {
            "sessions": sessions,
            "previews": previews.order_by("-ended_at")[:6],
            "query": query,
            "category": category,
            "categories": LiveSession.CATEGORY_CHOICES,
        },
    )


@login_required
def creator_profile(request, username):
    return redirect("user_profile", username=username)


@login_required
def create_session(request):
    if request.method == "POST":
        form = LiveSessionForm(request.POST, request.FILES)

        if form.is_valid():
            action = request.POST.get("action", "schedule")
            if action == "start" and not form.cleaned_data["recording_consent"]:
                form.add_error(
                    "recording_consent",
                    "Agree to the recording notice before going live.",
                )
            elif action == "start":
                try:
                    check_preview_recording_settings()
                except PreviewRecordingError as error:
                    form.add_error(None, str(error))
                else:
                    session = form.save(commit=False)
                    session.host = request.user
                    session.host_recording_consented_at = timezone.now()
                    session.save()
                    session.start()
                    _notify_live_session(session)
                    profile, _ = UserProfile.objects.get_or_create(
                        user=request.user
                    )
                    if not profile.is_host:
                        profile.is_host = True
                        profile.save(update_fields=["is_host"])

                    django_messages.success(
                        request,
                        "Your live session is ready.",
                    )
                    return redirect(
                        "session_detail",
                        room_name=session.room_name,
                    )
            elif action == "schedule" and not form.cleaned_data.get("scheduled_at"):
                form.add_error("scheduled_at", "Choose a date and time to schedule this session.")
            else:
                session = form.save(commit=False)
                session.host = request.user
                session.save()
                if action == "start":
                    session.start()
                    profile, _ = UserProfile.objects.get_or_create(
                        user=request.user
                    )
                    if not profile.is_host:
                        profile.is_host = True
                        profile.save(update_fields=["is_host"])

                django_messages.success(
                    request,
                    "Your live session is ready."
                    if session.status == "live"
                    else "Your session has been scheduled.",
                )

                return redirect(
                    "session_detail",
                    room_name=session.room_name,
                )
    else:
        form = LiveSessionForm()

    return render(
        request,
        "streaming/create_session.html",
        {"form": form},
    )


@login_required
def my_sessions(request):
    sessions = LiveSession.objects.filter(host=request.user)
    status = request.GET.get("status", "")
    valid_statuses = {choice[0] for choice in LiveSession.STATUS_CHOICES}
    if status in valid_statuses:
        sessions = sessions.filter(status=status)
    sessions = sessions.order_by("-created_at")

    return render(
        request,
        "streaming/my_sessions.html",
        {"sessions": sessions, "status": status},
    )


@login_required
def session_detail(request, room_name):
    session = get_object_or_404(
        LiveSession.objects.select_related("host"),
        room_name=room_name,
    )

    if (
        session.visibility == "private"
        and session.host_id != request.user.id
    ):
        raise PermissionDenied(
            "You do not have permission to view this private session."
        )

    participants = SessionParticipant.objects.filter(
        session=session,
        left_at__isnull=True,
    ).select_related("user", "user__profile")
    more_sessions = LiveSession.objects.filter(
        visibility="public",
        status__in=["live", "scheduled"],
        category=session.category,
    ).exclude(pk=session.pk).select_related("host")[:6]
    if session.preview_status == "processing":
        try:
            refresh_preview_recording(session)
        except PreviewRecordingError:
            logger.exception(
                "Could not check replay state for session=%s",
                session.room_name,
            )
    preview_url = ""
    if session.preview_status == "ready":
        try:
            preview_url = preview_video_url(session)
        except PreviewRecordingError:
            logger.exception(
                "Could not create replay URL for session=%s",
                session.room_name,
            )
            django_messages.error(
                request,
                "The replay is temporarily unavailable.",
            )

    return render(
        request,
        "streaming/session_detail.html",
        {
            "session": session,
            "participants": participants,
            "more_sessions": more_sessions,
            "is_favorite": FavoriteSession.objects.filter(
                user=request.user,
                session=session,
            ).exists(),
            "is_following": CreatorFollow.objects.filter(
                follower=request.user,
                following=session.host,
            ).exists(),
            "preview_url": preview_url,
            "preview_likes_count": PreviewLike.objects.filter(
                session=session,
            ).count(),
            "is_preview_liked": PreviewLike.objects.filter(
                session=session,
                user=request.user,
            ).exists(),
            "preview_comments": PreviewComment.objects.filter(
                session=session,
            ).select_related("user", "user__profile").order_by("-created_at")[:50],
            "preview_comment_form": PreviewCommentForm(),
        },
    )


@login_required
@require_POST
def start_session(request, room_name):
    session = get_object_or_404(
        LiveSession,
        room_name=room_name,
        host=request.user,
    )

    if session.status == "ended":
        django_messages.error(
            request,
            "This session has already ended.",
        )
        return redirect(
            "session_detail",
            room_name=session.room_name,
        )

    if (
        session.status == "scheduled"
        or session.host_recording_consented_at is None
    ):
        if request.POST.get("recording_consent") != "on":
            django_messages.error(
                request,
                "Agree to the recording notice before going live.",
            )
            return redirect(
                "session_detail",
                room_name=session.room_name,
            )
        try:
            check_preview_recording_settings()
        except PreviewRecordingError as error:
            django_messages.error(request, str(error))
            return redirect(
                "session_detail",
                room_name=session.room_name,
            )
        session.host_recording_consented_at = timezone.now()
        session.save(update_fields=["host_recording_consented_at"])

    if session.status == "scheduled":
        session.start()
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        if not profile.is_host:
            profile.is_host = True
            profile.save(update_fields=["is_host"])
        _notify_live_session(session)

    # The host must also have an active participant record
    # before requesting a LiveKit token.
    participant, created = SessionParticipant.objects.get_or_create(
        session=session,
        user=request.user,
        defaults={
            "recording_consented_at": session.host_recording_consented_at,
        },
    )

    if (
        participant.left_at is not None
        or participant.recording_consented_at is None
        or not participant.livekit_identity
    ):
        participant.left_at = None
        participant.recording_consented_at = session.host_recording_consented_at
        participant.save()

    return redirect(
        "join_session",
        room_name=session.room_name,
    )


@login_required
@require_POST
def end_session(request, room_name):
    session = get_object_or_404(
        LiveSession,
        room_name=room_name,
        host=request.user,
    )

    if session.status != "ended":
        recording_finished = _finish_preview_recording(request, session)
        session.end()

        SessionParticipant.objects.filter(
            session=session,
            left_at__isnull=True,
        ).update(left_at=timezone.now())
        async_to_sync(get_channel_layer().group_send)(
            f"chat_{session.room_name.hex}",
            {"type": "session_ended"},
        )
    else:
        recording_finished = True

    if recording_finished:
        django_messages.success(request, "Your live session has ended.")

    return redirect(
        "session_detail",
        room_name=session.room_name,
    )


@login_required
def join_session(request, room_name):
    session = get_object_or_404(
        LiveSession,
        room_name=room_name,
    )

    if session.status != "live":
        django_messages.error(
            request,
            "This session is not currently live.",
        )
        return redirect(
            "session_detail",
            room_name=session.room_name,
        )

    if (
        session.visibility == "private"
        and session.host_id != request.user.id
    ):
        raise PermissionDenied(
            "You cannot join this private session."
        )

    is_host = session.host_id == request.user.id
    if not is_host and session.preview_status != "recording":
        django_messages.info(
            request,
            "The host is preparing the recorded room. Please try again shortly.",
        )
        return redirect(
            "session_detail",
            room_name=session.room_name,
        )

    participant = SessionParticipant.objects.filter(
        session=session,
        user=request.user,
    ).first()
    if session.is_locked and not is_host and participant is None:
        django_messages.error(
            request,
            "The host has locked this room to new participants.",
        )
        return redirect(
            "session_detail",
            room_name=session.room_name,
        )
    if is_host:
        consented_at = session.host_recording_consented_at
    else:
        consented_at = (
            participant.recording_consented_at
            if participant is not None
            else None
        )

    consent_error = ""
    if consented_at is None:
        if request.method == "POST" and request.POST.get("recording_consent") == "on":
            consented_at = timezone.now()
            if is_host:
                session.host_recording_consented_at = consented_at
                session.save(update_fields=["host_recording_consented_at"])
        else:
            consent_error = (
                "You must agree to the recording notice before joining."
                if request.method == "POST"
                else ""
            )
            return render(
                request,
                "streaming/recording_consent.html",
                {
                    "session": session,
                    "is_host": is_host,
                    "consent_error": consent_error,
                },
            )

    # Lock the session row while checking capacity so two
    # simultaneous requests cannot exceed the participant limit.
    with transaction.atomic():
        locked_session = LiveSession.objects.select_for_update().get(
            pk=session.pk
        )

        participant = SessionParticipant.objects.filter(
            session=locked_session,
            user=request.user,
        ).first()

        if (
            locked_session.is_locked
            and locked_session.host_id != request.user.id
            and participant is None
        ):
            django_messages.error(
                request,
                "The host has locked this room to new participants.",
            )
            return redirect(
                "session_detail",
                room_name=locked_session.room_name,
            )

        if participant is None:
            active_count = SessionParticipant.objects.filter(
                session=locked_session,
                left_at__isnull=True,
            ).count()

            if active_count >= locked_session.participant_limit:
                django_messages.error(
                    request,
                    "This live session has reached its participant limit.",
                )
                return redirect(
                    "session_detail",
                    room_name=locked_session.room_name,
                )

            participant = SessionParticipant.objects.create(
                session=locked_session,
                user=request.user,
                recording_consented_at=consented_at,
            )

        elif participant.left_at is not None:
            active_count = SessionParticipant.objects.filter(
                session=locked_session,
                left_at__isnull=True,
            ).count()

            if active_count >= locked_session.participant_limit:
                django_messages.error(
                    request,
                    "This live session has reached its participant limit.",
                )
                return redirect(
                    "session_detail",
                    room_name=locked_session.room_name,
                )

            participant.left_at = None
            participant.is_muted = False
            participant.recording_consented_at = consented_at
            participant.save(update_fields=[
                "left_at",
                "is_muted",
                "recording_consented_at",
            ])

        elif not participant.livekit_identity:
            participant.recording_consented_at = consented_at
            participant.save()
        elif participant.recording_consented_at is None:
            participant.recording_consented_at = consented_at
            participant.save(update_fields=["recording_consented_at"])

    return render(
        request,
        "streaming/room.html",
        {
            "session": locked_session,
            "livekit_url": settings.LIVEKIT_URL,
            "is_host": is_host,
        },
    )


@login_required
def session_token(request, room_name):
    session = get_object_or_404(
        LiveSession,
        room_name=room_name,
        status="live",
    )

    participant = get_object_or_404(
        SessionParticipant,
        session=session,
        user=request.user,
        left_at__isnull=True,
    )
    if (
        session.host_recording_consented_at is None
        or participant.recording_consented_at is None
    ):
        return JsonResponse(
            {"error": "Consent to the session recording before joining."},
            status=403,
        )
    if session.host_id != request.user.id and session.preview_status != "recording":
        return JsonResponse(
            {"error": "The host is preparing the recorded room."},
            status=409,
        )

    try:
        token = create_livekit_token(
            session,
            request.user,
            participant,
        )
    except RuntimeError as error:
        logger.exception("LiveKit token configuration error")
        return JsonResponse(
            {"error": str(error)},
            status=503,
        )

    return JsonResponse({
        "token": token,
        "url": settings.LIVEKIT_URL,
    })


@login_required
@require_POST
def leave_session(request, room_name):
    session = get_object_or_404(
        LiveSession,
        room_name=room_name,
        status="live",
    )

    if session.host_id == request.user.id:
        _finish_preview_recording(request, session)
        session.end()
        SessionParticipant.objects.filter(
            session=session,
            left_at__isnull=True,
        ).update(left_at=timezone.now())
        async_to_sync(get_channel_layer().group_send)(
            f"chat_{session.room_name.hex}",
            {"type": "session_ended"},
        )
    else:
        SessionParticipant.objects.filter(
            session=session,
            user=request.user,
            left_at__isnull=True,
        ).update(left_at=timezone.now(), is_muted=False)

    return redirect(
        "session_detail",
        room_name=session.room_name,
    )


@login_required
def room_status(request, room_name):
    session = get_object_or_404(
        LiveSession,
        room_name=room_name,
        status="live",
    )

    current = SessionParticipant.objects.filter(
        session=session,
        user=request.user,
        left_at__isnull=True,
    ).exists()

    if session.host_id == request.user.id:
        current = True

    if (
        session.visibility == "private"
        and session.host_id != request.user.id
    ) or not current:
        raise PermissionDenied(
            "You do not have permission to view this room's participants."
        )

    participants = SessionParticipant.objects.filter(
        session=session,
        left_at__isnull=True,
    ).select_related("user", "user__profile")

    participant_data = []

    for participant in participants:
        profile = getattr(
            participant.user,
            "profile",
            None,
        )
        if profile is None:
            profile, _ = UserProfile.objects.get_or_create(
                user=participant.user,
            )
        elif not profile.username:
            profile.save(update_fields=["username"])

        participant_data.append({
            "id": participant.user_id,
            "name": (
                profile.full_name
                if profile and profile.full_name
                else participant.user.get_username()
            ),
            "livekit_identity": participant.livekit_identity,
            "profile_url": reverse(
                "user_profile",
                kwargs={"username": profile.username},
            ),
            "avatar_url": profile.avatar.url if profile.avatar else "",
            "is_self": participant.user_id == request.user.id,
            "is_host": participant.user_id == session.host_id,
            "is_muted": participant.is_muted,
        })

    return JsonResponse({
        "active": current,
        "participants": participant_data,
        "count": len(participant_data),
        "limit": session.participant_limit,
        "is_locked": session.is_locked,
        "chat_enabled": session.chat_enabled,
    })


@login_required
@require_POST
def room_controls(request, room_name):
    session = get_object_or_404(
        LiveSession,
        room_name=room_name,
        host=request.user,
        status="live",
    )
    action = request.POST.get("action")

    if action == "toggle_lock":
        session.is_locked = not session.is_locked
        session.save(update_fields=["is_locked"])
        return JsonResponse({"is_locked": session.is_locked})

    if action == "toggle_chat":
        session.chat_enabled = not session.chat_enabled
        session.save(update_fields=["chat_enabled"])
        async_to_sync(get_channel_layer().group_send)(
            f"chat_{session.room_name.hex}",
            {
                "type": "chat_settings_changed",
                "enabled": session.chat_enabled,
            },
        )
        return JsonResponse({"chat_enabled": session.chat_enabled})

    if action == "mute_all":
        participants = list(
            SessionParticipant.objects.filter(
                session=session,
                left_at__isnull=True,
            )
        )
        try:
            muted_identities = mute_all_livekit_participants(session)
        except Exception:
            logger.exception(
                "LiveKit mute-all failed: session=%s",
                session.pk,
            )
            return JsonResponse(
                {"error": "LiveKit could not mute all room participants."},
                status=502,
            )

        SessionParticipant.objects.filter(
            pk__in=[
                participant.pk for participant in participants
                if participant.livekit_identity in muted_identities
            ]
        ).update(is_muted=True)
        return JsonResponse({
            "success": True,
            "muted_count": len(muted_identities),
        })

    return JsonResponse({"error": "Unknown room control."}, status=400)


@login_required
@require_POST
def toggle_participant_mute(request, room_name, user_id):
    session = get_object_or_404(
        LiveSession,
        room_name=room_name,
        host=request.user,
        status="live",
    )
    if user_id == request.user.id:
        return JsonResponse(
            {"error": "Use your microphone control to mute yourself."},
            status=400,
        )
    participant = get_object_or_404(
        SessionParticipant,
        session=session,
        user_id=user_id,
        left_at__isnull=True,
    )
    muted = not participant.is_muted
    try:
        set_livekit_participant_muted(
            session,
            participant.livekit_identity,
            muted,
        )
    except Exception:
        logger.exception(
            "LiveKit participant mute failed: session=%s user=%s",
            session.pk,
            user_id,
        )
        return JsonResponse(
            {"error": "LiveKit could not update this participant's microphone."},
            status=502,
        )

    participant.is_muted = muted
    participant.save(update_fields=["is_muted"])
    return JsonResponse({"is_muted": muted})


@login_required
@require_POST
def update_microphone_status(request, room_name):
    participant = get_object_or_404(
        SessionParticipant,
        session__room_name=room_name,
        session__status="live",
        user=request.user,
        left_at__isnull=True,
    )
    value = request.POST.get("muted")
    if value not in {"true", "false"}:
        return JsonResponse(
            {"error": "Microphone status must be true or false."},
            status=400,
        )
    participant.is_muted = value == "true"
    participant.save(update_fields=["is_muted"])
    return JsonResponse({"is_muted": participant.is_muted})


@login_required
@require_POST
def remove_participant(request, room_name, user_id):
    session = get_object_or_404(
        LiveSession,
        room_name=room_name,
        host=request.user,
        status="live",
    )

    if user_id == request.user.id:
        return JsonResponse(
            {"error": "You cannot remove yourself as host."},
            status=400,
        )

    participant = get_object_or_404(
        SessionParticipant,
        session=session,
        user_id=user_id,
        left_at__isnull=True,
    )

    # First disable the participant in Entstream.
    participant.left_at = timezone.now()
    participant.is_muted = False
    participant.save(update_fields=["left_at", "is_muted"])

    # Then request an actual disconnection from LiveKit.
    try:
        remove_livekit_participant(
            session,
            participant.livekit_identity,
        )
    except Exception:
        logger.exception(
            "LiveKit participant removal failed: session=%s user=%s",
            session.pk,
            user_id,
        )

        return JsonResponse(
            {
                "error": (
                    "Entstream access was disabled, but LiveKit "
                    "disconnection could not be confirmed."
                )
            },
            status=502,
        )

    return JsonResponse({"success": True})
