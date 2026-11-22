import json
import logging
import time
import uuid

import cloudinary
import cloudinary.api
import cloudinary.uploader
import cloudinary.utils
from cloudinary.exceptions import Error as CloudinaryError
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import F, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import UserProfile
from .forms import VideoUploadForm
from .mpesa import tip_template_context, user_display_name
from .models import UploadedVideo


logger = logging.getLogger(__name__)
ALLOWED_VIDEO_FORMATS = {"mp4", "webm"}
def _cloudinary_is_configured():
    return all((
        settings.CLOUDINARY_CLOUD_NAME,
        settings.CLOUDINARY_API_KEY,
        settings.CLOUDINARY_API_SECRET,
    ))


def _configure_cloudinary():
    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )


def _owner_public_id_prefix(user):
    return f"radevlive/videos/user_{user.pk}"


def _is_owned_public_id(public_id, user):
    prefix = _owner_public_id_prefix(user) + "/"
    if not isinstance(public_id, str) or not public_id.startswith(prefix):
        return False
    identifier = public_id[len(prefix):]
    try:
        return uuid.UUID(hex=identifier).hex == identifier
    except ValueError:
        return False


def _upload_context(form, uploaded_public_id=""):
    return {
        "form": form,
        "uploaded_public_id": uploaded_public_id,
        "cloudinary_cloud_name": settings.CLOUDINARY_CLOUD_NAME,
        "cloudinary_api_key": settings.CLOUDINARY_API_KEY,
        "max_video_size": settings.VIDEO_UPLOAD_MAX_BYTES,
        "cloudinary_configured": _cloudinary_is_configured(),
    }


def video_library(request):
    videos = UploadedVideo.objects.select_related(
        "owner",
        "owner__profile",
    )
    query = request.GET.get("q", "").strip()
    creator = request.GET.get("creator", "").strip()
    if query:
        videos = videos.filter(
            Q(title__icontains=query) | Q(description__icontains=query)
        )
    if creator:
        videos = videos.filter(owner__profile__username=creator)
    videos = Paginator(videos, 24).get_page(request.GET.get("page"))
    return render(
        request,
        "streaming/video_library.html",
        {"videos": videos, "query": query, "creator": creator},
    )


@login_required
def video_upload(request):
    form = VideoUploadForm(request.POST or None)
    public_id = (
        request.POST.get("public_id", "").strip()
        if request.method == "POST"
        else ""
    )
    if request.method == "POST":
        if form.is_valid():
            if not _is_owned_public_id(public_id, request.user):
                form.add_error(
                    None,
                    "Upload a video with the uploader on this page before saving.",
                )
            elif not _cloudinary_is_configured():
                form.add_error(
                    None,
                    "Video uploads are not configured on this server.",
                )
                return render(
                    request,
                    "streaming/video_upload.html",
                    _upload_context(form, public_id),
                    status=503,
                )
            else:
                _configure_cloudinary()
                try:
                    asset = cloudinary.api.resource(
                        public_id,
                        resource_type="video",
                        type="upload",
                    )
                except CloudinaryError:
                    logger.exception(
                        "Could not verify Cloudinary upload for user=%s",
                        request.user.pk,
                    )
                    form.add_error(
                        None,
                        "We could not verify the uploaded video. Please try again.",
                    )
                    return render(
                        request,
                        "streaming/video_upload.html",
                        _upload_context(form, public_id),
                        status=502,
                    )

                asset_size = asset.get("bytes")
                asset_format = asset.get("format", "").lower()
                if (
                    asset.get("resource_type") != "video"
                    or asset.get("type") != "upload"
                    or asset.get("public_id") != public_id
                    or asset_format not in ALLOWED_VIDEO_FORMATS
                    or not isinstance(asset_size, int)
                    or asset_size < 1
                    or asset_size > settings.VIDEO_UPLOAD_MAX_BYTES
                ):
                    form.add_error(
                        None,
                        "The uploaded asset could not be validated. "
                        "Choose an MP4 or WebM video up to 1 GB.",
                    )
                elif not asset.get("secure_url"):
                    form.add_error(
                        None,
                        "Cloudinary did not return a secure playback URL.",
                    )
                else:
                    existing = UploadedVideo.objects.filter(
                        cloudinary_public_id=public_id,
                    ).first()
                    if existing:
                        if existing.owner_id == request.user.pk:
                            return redirect("video_detail", video_id=existing.pk)
                        form.add_error(
                            None,
                            "This uploaded asset is already associated with another account.",
                        )
                    else:
                        UserProfile.objects.get_or_create(user=request.user)
                        thumbnail_url, _ = cloudinary.utils.cloudinary_url(
                            public_id,
                            cloud_name=settings.CLOUDINARY_CLOUD_NAME,
                            resource_type="video",
                            format="jpg",
                            transformation=[{
                                "start_offset": 0.5,
                                "width": 640,
                                "height": 360,
                                "crop": "fill",
                            }],
                            secure=True,
                        )
                        video = form.save(commit=False)
                        video.owner = request.user
                        video.cloudinary_public_id = public_id
                        video.video_url = asset["secure_url"]
                        video.thumbnail_url = thumbnail_url
                        video.format = asset_format
                        video.size_bytes = asset_size
                        video.duration_seconds = asset.get("duration")
                        try:
                            video.save()
                        except IntegrityError:
                            logger.exception(
                                "Duplicate Cloudinary video public ID=%s",
                                public_id,
                            )
                            form.add_error(
                                None,
                                "This video has already been saved.",
                            )
                        else:
                            messages.success(request, "Your video is now published.")
                            return redirect("video_detail", video_id=video.pk)

    return render(
        request,
        "streaming/video_upload.html",
        _upload_context(form, public_id),
        status=400 if request.method == "POST" and form.errors else 200,
    )


@login_required
@require_POST
def video_upload_signature(request):
    if not _cloudinary_is_configured():
        return JsonResponse(
            {"error": "Video uploads are not configured on this server."},
            status=503,
        )
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid signature request."}, status=400)

    if not isinstance(data, dict) or set(data) not in (set(), {"public_id"}):
        return JsonResponse({"error": "Invalid upload parameters."}, status=400)
    public_id = data.get("public_id")
    if public_id is None:
        public_id = (
            f"{_owner_public_id_prefix(request.user)}/{uuid.uuid4().hex}"
        )
    elif not _is_owned_public_id(public_id, request.user):
        return JsonResponse({"error": "Invalid upload parameters."}, status=400)

    timestamp = int(time.time())
    params = {"public_id": public_id, "timestamp": timestamp}
    signature = cloudinary.utils.api_sign_request(
        params,
        settings.CLOUDINARY_API_SECRET,
    )
    return JsonResponse({
        "public_id": public_id,
        "timestamp": timestamp,
        "signature": signature,
    })


def video_detail(request, video_id):
    video = get_object_or_404(
        UploadedVideo.objects.select_related("owner", "owner__profile"),
        pk=video_id,
    )
    UploadedVideo.objects.filter(pk=video.pk).update(
        view_count=F("view_count") + 1,
    )
    video.refresh_from_db(fields=["view_count"])
    return render(
        request,
        "streaming/video_detail.html",
        {
            "video": video,
            "tip_target_type": "video",
            "tip_target_id": video.pk,
            "tip_recipient_name": user_display_name(video.owner),
            **tip_template_context(),
        },
    )


@login_required
@require_POST
def delete_video(request, video_id):
    video = get_object_or_404(
        UploadedVideo,
        pk=video_id,
        owner=request.user,
    )
    if not _cloudinary_is_configured():
        messages.error(request, "Video storage is not configured on this server.")
        return redirect("video_detail", video_id=video.pk)

    _configure_cloudinary()
    try:
        result = cloudinary.uploader.destroy(
            video.cloudinary_public_id,
            resource_type="video",
            type="upload",
            invalidate=True,
        )
    except CloudinaryError:
        logger.exception("Could not delete Cloudinary video=%s", video.pk)
        messages.error(request, "The video could not be deleted. Please try again.")
        return redirect("video_detail", video_id=video.pk)
    if result.get("result") != "ok":
        logger.error(
            "Cloudinary did not confirm deletion for video=%s: %s",
            video.pk,
            result.get("result"),
        )
        messages.error(request, "The video could not be deleted. Please try again.")
        return redirect("video_detail", video_id=video.pk)

    video.delete()
    messages.success(request, "Your video has been deleted.")
    return redirect("video_library")
