from django.db import migrations


def use_later_thumbnail_frame(apps, schema_editor):
    UploadedVideo = apps.get_model("streaming", "UploadedVideo")
    videos = UploadedVideo.objects.filter(thumbnail_url__contains=",so_0,")
    for video in videos.iterator():
        video.thumbnail_url = video.thumbnail_url.replace(
            ",so_0,",
            ",so_0.5,",
        )
        video.save(update_fields=["thumbnail_url"])


def restore_first_thumbnail_frame(apps, schema_editor):
    UploadedVideo = apps.get_model("streaming", "UploadedVideo")
    videos = UploadedVideo.objects.filter(thumbnail_url__contains=",so_0.5,")
    for video in videos.iterator():
        video.thumbnail_url = video.thumbnail_url.replace(
            ",so_0.5,",
            ",so_0,",
        )
        video.save(update_fields=["thumbnail_url"])


class Migration(migrations.Migration):

    dependencies = [
        ("streaming", "0008_uploadedvideo_view_count"),
    ]

    operations = [
        migrations.RunPython(
            use_later_thumbnail_frame,
            restore_first_thumbnail_frame,
        ),
    ]
