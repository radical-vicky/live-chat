from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib import messages
from django.urls import reverse

from .forms import ProfileForm
from .models import UserProfile
from streaming.models import CreatorFollow, LiveSession, UploadedVideo


def _compact_count(value):
    if value < 1000:
        return str(value)
    if value >= 1_000_000:
        amount = f"{value / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{amount}M"
    amount = f"{value / 1000:.1f}".rstrip("0").rstrip(".")
    return f"{amount}K"


@login_required
def profile(request):
    user_profile, _ = UserProfile.objects.get_or_create(
        user=request.user
    )

    if request.method == "POST":
        form = ProfileForm(
            request.POST,
            request.FILES,
            instance=user_profile
        )

        if form.is_valid():
            profile = form.save()
            request.user.first_name = profile.full_name
            request.user.save(update_fields=["first_name"])
            messages.success(request, "Your profile has been updated.")
            return redirect(f"{reverse('profile')}?tab=settings")
    else:
        form = ProfileForm(instance=user_profile)

    tab = request.GET.get("tab", "sessions")
    if tab not in {"sessions", "videos", "about", "settings"}:
        tab = "sessions"
    sessions = LiveSession.objects.filter(
        host=request.user,
        visibility="public",
        status__in=["live", "scheduled"],
    ).select_related("host").order_by("-created_at")
    previews = LiveSession.objects.filter(
        host=request.user,
        visibility="public",
        status="ended",
        preview_status="ready",
    ).select_related("host").order_by("-ended_at")
    video_count = UploadedVideo.objects.filter(owner=request.user).count()
    videos = UploadedVideo.objects.filter(owner=request.user)[:12]
    followers_count = CreatorFollow.objects.filter(
        following=request.user
    ).count()
    following_count = CreatorFollow.objects.filter(
        follower=request.user
    ).count()
    session_count = sessions.count()
    return render(request, "account/profile.html", {
        "form": form,
        "profile_user": request.user,
        "profile": user_profile,
        "sessions": sessions,
        "previews": previews,
        "videos": videos,
        "video_count": video_count,
        "followers_count": followers_count,
        "following_count": following_count,
        "session_count": session_count,
        "followers_display": _compact_count(followers_count),
        "following_display": _compact_count(following_count),
        "sessions_display": _compact_count(session_count),
        "tab": tab,
        "is_owner": True,
    })


@login_required
def user_profile(request, username):
    profile_user = get_object_or_404(
        get_user_model().objects.select_related("profile"),
        profile__username=username,
        is_active=True,
    )
    if profile_user == request.user:
        return redirect("profile")

    profile = profile_user.profile
    sessions = LiveSession.objects.filter(
        host=profile_user,
        visibility="public",
        status__in=["live", "scheduled"],
    ).select_related("host").order_by("-created_at")
    previews = LiveSession.objects.filter(
        host=profile_user,
        visibility="public",
        status="ended",
        preview_status="ready",
    ).select_related("host").order_by("-ended_at")
    video_count = UploadedVideo.objects.filter(owner=profile_user).count()
    videos = UploadedVideo.objects.filter(owner=profile_user)[:12]
    followers_count = CreatorFollow.objects.filter(
        following=profile_user
    ).count()
    following_count = CreatorFollow.objects.filter(
        follower=profile_user
    ).count()
    session_count = sessions.count()
    tab = request.GET.get("tab", "sessions")
    if tab not in {"sessions", "videos", "about"}:
        tab = "sessions"

    return render(request, "account/profile.html", {
        "form": None,
        "profile_user": profile_user,
        "profile": profile,
        "sessions": sessions,
        "previews": previews,
        "videos": videos,
        "video_count": video_count,
        "followers_count": followers_count,
        "following_count": following_count,
        "session_count": session_count,
        "followers_display": _compact_count(followers_count),
        "following_display": _compact_count(following_count),
        "sessions_display": _compact_count(session_count),
        "tab": tab,
        "is_owner": False,
        "is_following": CreatorFollow.objects.filter(
            follower=request.user,
            following=profile_user,
        ).exists(),
    })