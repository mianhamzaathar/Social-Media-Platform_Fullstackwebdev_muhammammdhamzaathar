from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth import logout
from django.shortcuts import redirect, render
from django.contrib.auth.models import User
from .models import Profile
from django.views.decorators.http import require_http_methods

def follow_user(request, username):

    user = User.objects.get(username=username)

    profile = Profile.objects.get(user=user)

    if request.user in profile.followers.all():

        profile.followers.remove(request.user)

    else:

        profile.followers.add(request.user)

    return redirect("profile", username=username)

def profile(request, username):

    user = User.objects.get(username=username)

    profile = Profile.objects.get(user=user)

    return render(request,"profile.html",{

        "profile":profile

    })


@require_http_methods(["GET", "POST"])
def signup(request):
    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("/login/")

    return render(request, "registration/register.html", {"form": form})


def logout_user(request):
    logout(request)
    return redirect("/login/")
