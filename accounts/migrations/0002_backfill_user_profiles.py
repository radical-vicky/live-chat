from django.conf import settings
from django.db import migrations
from django.utils.text import slugify


def create_missing_profiles(apps, schema_editor):
    profile_model = apps.get_model("accounts", "UserProfile")
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    user_model = apps.get_model(app_label, model_name)
    existing_user_ids = set(
        profile_model.objects.values_list("user_id", flat=True)
    )
    used_usernames = set(
        profile_model.objects.values_list("username", flat=True)
    )
    profiles = []

    for user in user_model.objects.only("id", "username", "email").iterator():
        if user.pk in existing_user_ids:
            continue
        base = slugify(user.username or user.email.split("@")[0]) or "user"
        username = base
        suffix = 1
        while username in used_usernames:
            username = f"{base}-{suffix}"
            suffix += 1
        used_usernames.add(username)
        profiles.append(
            profile_model(user_id=user.pk, username=username)
        )

    profile_model.objects.bulk_create(profiles)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            create_missing_profiles,
            migrations.RunPython.noop,
        ),
    ]
