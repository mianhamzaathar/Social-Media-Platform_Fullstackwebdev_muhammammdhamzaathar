from django.contrib import admin

# Register your models here.
from django.contrib import admin
from .models import *

admin.site.register(Profile)
admin.site.register(Post)
admin.site.register(Comment)
admin.site.register(Like)
admin.site.register(Follow)
admin.site.register(Notification)
admin.site.register(Story)
admin.site.register(Message)