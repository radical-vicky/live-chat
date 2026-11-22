
from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify


class UserProfile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profile'
    )

    full_name = models.CharField(max_length=150, blank=True)
    username = models.SlugField(max_length=150, unique=True, blank=True)
    bio = models.TextField(max_length=500, blank=True)
    avatar = models.ImageField(
        upload_to='profiles/',
        blank=True,
        null=True
    )

    is_host = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.username:
            base = slugify(self.user.username or self.user.email.split('@')[0])
            candidate = base or 'user'
            suffix = 1

            while UserProfile.objects.filter(
                username=candidate
            ).exclude(pk=self.pk).exists():
                candidate = f'{base or "user"}-{suffix}'
                suffix += 1

            self.username = candidate

        super().save(*args, **kwargs)

    def __str__(self):
        return self.user.email
