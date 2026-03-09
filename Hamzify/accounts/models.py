from django.db import models
from django.contrib.auth.models import User

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="accounts_profile")
    bio = models.TextField(blank=True)
    profile_pic = models.ImageField(upload_to="profile_pics", blank=True)
    followers = models.ManyToManyField(User, related_name="following", blank=True)  # unique reverse

    def __str__(self):
        return self.user.username
