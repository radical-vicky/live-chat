
import asyncio
import uuid
from datetime import timedelta
from urllib.parse import quote

import aiohttp
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from livekit import api


class PreviewRecordingError(RuntimeError):
    pass


def check_livekit_settings():
    required = [
        settings.LIVEKIT_URL,
        settings.LIVEKIT_API_KEY,
        settings.LIVEKIT_API_SECRET,
    ]

    if not all(required):
        raise RuntimeError(
            "LiveKit credentials are missing from your environment."
        )


def check_preview_recording_settings():
    try:
        check_livekit_settings()
    except RuntimeError as error:
        raise PreviewRecordingError(str(error)) from error
    required = [
        settings.LIVEKIT_RECORDING_S3_BUCKET,
        settings.LIVEKIT_RECORDING_S3_REGION,
        settings.LIVEKIT_RECORDING_S3_ACCESS_KEY,
        settings.LIVEKIT_RECORDING_S3_SECRET,
    ]
    if not all(required):
        raise PreviewRecordingError(
            "Replay recording is not configured. Set "
            "LIVEKIT_RECORDING_S3_BUCKET, LIVEKIT_RECORDING_S3_ACCESS_KEY, "
            "and LIVEKIT_RECORDING_S3_SECRET in the server environment."
        )


def _recording_s3_client():
    return boto3.client(
        "s3",
        region_name=settings.LIVEKIT_RECORDING_S3_REGION,
        endpoint_url=settings.LIVEKIT_RECORDING_S3_ENDPOINT or None,
        aws_access_key_id=settings.LIVEKIT_RECORDING_S3_ACCESS_KEY,
        aws_secret_access_key=settings.LIVEKIT_RECORDING_S3_SECRET,
        config=(
            Config(s3={"addressing_style": "path"})
            if settings.LIVEKIT_RECORDING_S3_FORCE_PATH_STYLE
            else None
        ),
    )


def start_preview_recording(session):
    check_preview_recording_settings()
    if session.preview_status == "recording" and session.preview_egress_id:
        return session.preview_egress_id

    object_key = f"previews/{session.room_name.hex}/{uuid.uuid4().hex}.mp4"
    output = api.EncodedFileOutput(
        filepath=object_key,
        s3=api.S3Upload(
            access_key=settings.LIVEKIT_RECORDING_S3_ACCESS_KEY,
            secret=settings.LIVEKIT_RECORDING_S3_SECRET,
            region=settings.LIVEKIT_RECORDING_S3_REGION,
            bucket=settings.LIVEKIT_RECORDING_S3_BUCKET,
            endpoint=settings.LIVEKIT_RECORDING_S3_ENDPOINT,
            force_path_style=settings.LIVEKIT_RECORDING_S3_FORCE_PATH_STYLE,
        ),
    )

    async def start():
        async with api.LiveKitAPI(
            url=settings.LIVEKIT_URL,
            api_key=settings.LIVEKIT_API_KEY,
            api_secret=settings.LIVEKIT_API_SECRET,
        ) as livekit_api:
            return await livekit_api.egress.start_room_composite_egress(
                api.RoomCompositeEgressRequest(
                    room_name=session.room_name.hex,
                    layout="grid",
                    file_outputs=[output],
                )
            )

    try:
        info = asyncio.run(start())
    except (
        api.TwirpError,
        aiohttp.ClientError,
        asyncio.TimeoutError,
        ValueError,
    ) as error:
        raise PreviewRecordingError(
            "LiveKit could not start replay recording."
        ) from error

    session.preview_egress_id = info.egress_id
    session.preview_object_key = object_key
    session.preview_status = "recording"
    session.save(update_fields=[
        "preview_egress_id",
        "preview_object_key",
        "preview_status",
    ])
    return info.egress_id


def stop_preview_recording(session):
    if not session.preview_egress_id:
        raise PreviewRecordingError(
            "No active replay recording was found for this session."
        )
    try:
        check_livekit_settings()
    except RuntimeError as error:
        raise PreviewRecordingError(str(error)) from error

    async def stop():
        async with api.LiveKitAPI(
            url=settings.LIVEKIT_URL,
            api_key=settings.LIVEKIT_API_KEY,
            api_secret=settings.LIVEKIT_API_SECRET,
        ) as livekit_api:
            return await livekit_api.egress.stop_egress(
                api.StopEgressRequest(egress_id=session.preview_egress_id)
            )

    try:
        info = asyncio.run(stop())
    except (
        api.TwirpError,
        aiohttp.ClientError,
        asyncio.TimeoutError,
        ValueError,
    ) as error:
        raise PreviewRecordingError(
            "LiveKit could not finish replay recording."
        ) from error

    if info.status in (api.EGRESS_FAILED, api.EGRESS_ABORTED):
        session.preview_status = "failed"
        session.save(update_fields=["preview_status"])
        raise PreviewRecordingError("LiveKit could not finish replay recording.")

    session.preview_status = "processing"
    session.save(update_fields=["preview_status"])
    refresh_preview_recording(session)


def refresh_preview_recording(session):
    if session.preview_status != "processing" or not session.preview_object_key:
        return session.preview_status
    check_preview_recording_settings()

    try:
        _recording_s3_client().head_object(
            Bucket=settings.LIVEKIT_RECORDING_S3_BUCKET,
            Key=session.preview_object_key,
        )
    except ClientError as error:
        error_code = error.response.get("Error", {}).get("Code")
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            pass
        else:
            raise PreviewRecordingError(
                "The replay file could not be checked in storage."
            ) from error
    except BotoCoreError as error:
        raise PreviewRecordingError(
            "The replay file could not be checked in storage."
        ) from error
    else:
        session.preview_status = "ready"
        session.save(update_fields=["preview_status"])
        return session.preview_status

    async def get_egress_status():
        async with api.LiveKitAPI(
            url=settings.LIVEKIT_URL,
            api_key=settings.LIVEKIT_API_KEY,
            api_secret=settings.LIVEKIT_API_SECRET,
        ) as livekit_api:
            response = await livekit_api.egress.list_egress(
                api.ListEgressRequest(egress_id=session.preview_egress_id)
            )
            return response.items

    try:
        egress_items = asyncio.run(get_egress_status())
    except (
        api.TwirpError,
        aiohttp.ClientError,
        asyncio.TimeoutError,
        ValueError,
    ) as error:
        raise PreviewRecordingError(
            "The replay recording status could not be checked."
        ) from error

    if not egress_items or egress_items[0].status in (
        api.EGRESS_FAILED,
        api.EGRESS_ABORTED,
        api.EGRESS_COMPLETE,
    ):
        session.preview_status = "failed"
        session.save(update_fields=["preview_status"])
    return session.preview_status


def preview_video_url(session):
    if not session.preview_object_key:
        return ""
    check_preview_recording_settings()
    public_base_url = settings.LIVEKIT_RECORDING_S3_PUBLIC_BASE_URL.rstrip("/")
    if public_base_url:
        return f"{public_base_url}/{quote(session.preview_object_key, safe='/')}"
    try:
        return _recording_s3_client().generate_presigned_url(
            "get_object",
            Params={
                "Bucket": settings.LIVEKIT_RECORDING_S3_BUCKET,
                "Key": session.preview_object_key,
            },
            ExpiresIn=86400,
        )
    except BotoCoreError as error:
        raise PreviewRecordingError(
            "A playback URL could not be created for this replay."
        ) from error


def delete_preview_recording(session):
    if session.preview_object_key:
        check_preview_recording_settings()
        try:
            _recording_s3_client().delete_object(
                Bucket=settings.LIVEKIT_RECORDING_S3_BUCKET,
                Key=session.preview_object_key,
            )
        except BotoCoreError as error:
            raise PreviewRecordingError(
                "The replay could not be deleted from storage."
            ) from error


def create_livekit_token(session, user, participant):
    check_livekit_settings()

    if not participant.livekit_identity:
        raise RuntimeError(
            "This participant does not have a LiveKit identity."
        )

    profile = getattr(user, "profile", None)

    display_name = (
        getattr(profile, "full_name", "")
        or user.get_username()
    )

    token = (
        api.AccessToken(
            settings.LIVEKIT_API_KEY,
            settings.LIVEKIT_API_SECRET,
        )
        .with_identity(participant.livekit_identity)
        .with_name(display_name)
        .with_ttl(timedelta(minutes=30))
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=session.room_name.hex,
                can_publish=True,
                can_subscribe=True,
            )
        )
    )

    return token.to_jwt()


def remove_livekit_participant(session, identity):
    check_livekit_settings()

    if not identity:
        raise RuntimeError(
            "The participant does not have a LiveKit identity."
        )

    async def remove():
        async with api.LiveKitAPI(
            url=settings.LIVEKIT_URL,
            api_key=settings.LIVEKIT_API_KEY,
            api_secret=settings.LIVEKIT_API_SECRET,
        ) as livekit_api:
            await livekit_api.room.remove_participant(
                api.RoomParticipantIdentity(
                    room=session.room_name.hex,
                    identity=identity,
                )
            )

    asyncio.run(remove())


def set_livekit_participant_muted(session, identity, muted):
    check_livekit_settings()

    if not identity:
        raise RuntimeError(
            "The participant does not have a LiveKit identity."
        )

    async def set_muted():
        async with api.LiveKitAPI(
            url=settings.LIVEKIT_URL,
            api_key=settings.LIVEKIT_API_KEY,
            api_secret=settings.LIVEKIT_API_SECRET,
        ) as livekit_api:
            participant = await livekit_api.room.get_participant(
                api.RoomParticipantIdentity(
                    room=session.room_name.hex,
                    identity=identity,
                )
            )
            for track in participant.tracks:
                if track.type == api.TrackType.AUDIO and track.muted != muted:
                    await livekit_api.room.mute_published_track(
                        api.MuteRoomTrackRequest(
                            room=session.room_name.hex,
                            identity=identity,
                            track_sid=track.sid,
                            muted=muted,
                        )
                    )

    asyncio.run(set_muted())


def mute_all_livekit_participants(session):
    check_livekit_settings()

    async def mute_all():
        muted_identities = set()
        async with api.LiveKitAPI(
            url=settings.LIVEKIT_URL,
            api_key=settings.LIVEKIT_API_KEY,
            api_secret=settings.LIVEKIT_API_SECRET,
        ) as livekit_api:
            participants = await livekit_api.room.list_participants(
                api.ListParticipantsRequest(room=session.room_name.hex)
            )
            for participant in participants.participants:
                for track in participant.tracks:
                    if track.type == api.TrackType.AUDIO and not track.muted:
                        await livekit_api.room.mute_published_track(
                            api.MuteRoomTrackRequest(
                                room=session.room_name.hex,
                                identity=participant.identity,
                                track_sid=track.sid,
                                muted=True,
                            )
                        )
                        muted_identities.add(participant.identity)
        return muted_identities

    return asyncio.run(mute_all())
