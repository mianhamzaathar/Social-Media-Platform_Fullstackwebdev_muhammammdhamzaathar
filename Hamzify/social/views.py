from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
import json
from datetime import timedelta
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.db.models import Count
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.timesince import timesince
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_exempt

from .models import Comment, Follow, Like, Message, Notification, Post, Profile, Story


def _is_video_upload(upload):
    if not upload:
        return False
    content_type = (getattr(upload, "content_type", "") or "").lower()
    if content_type.startswith("video/"):
        return True
    name = (getattr(upload, "name", "") or "").lower()
    return name.endswith((".mp4", ".mov", ".mkv", ".avi", ".webm"))


def _safe_image_url(image_field):
    if not image_field:
        return ""
    try:
        return image_field.url
    except ValueError:
        return ""


def _ensure_profile(user):
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


def _attach_profile_fields(user):
    profile = _ensure_profile(user)
    user.profile_pic = profile.profile_pic
    user.bio = profile.bio
    user.full_name = user.get_full_name()
    user.website = ""
    return profile


def _enrich_post(post, request_user=None):
    post.likes_count = post.likes.count()
    post.comments_count = post.comments.count()
    if request_user and request_user.is_authenticated:
        post.user_has_liked = post.likes.filter(user=request_user).exists()
    else:
        post.user_has_liked = False
    post.user_has_saved = False
    return post


def _notify_user(user, message, actor=None):
    notification = Notification.objects.create(user=user, message=message)
    if not actor:
        return notification

    channel_layer = get_channel_layer()
    group_name = f"notify_{user.id}"
    async_to_sync(channel_layer.group_send)(
        group_name,
        {
            "type": "notify.message",
            "message": message,
            "created_at": notification.created_at.strftime("%b %d, %H:%M"),
            "actor": actor.username,
        },
    )
    return notification


def entrypoint(request):
    return render(request, "landing.html")


@login_required
@ensure_csrf_cookie
def home(request):
    posts_qs = (
        Post.objects.select_related("user")
        .prefetch_related("likes", "comments", "comments__user")
        .all()
    )
    paginator = Paginator(posts_qs, 10)
    posts = paginator.get_page(request.GET.get("page", 1))
    saved_post_ids = set(request.session.get("saved_post_ids", []))
    for post in posts:
        _attach_profile_fields(post.user)
        _enrich_post(post, request.user)
        post.user_has_saved = post.id in saved_post_ids

    stories = Story.objects.select_related("user").order_by("-created_at")[:20]
    for story in stories:
        _attach_profile_fields(story.user)

    suggested_users = User.objects.exclude(id=getattr(request.user, "id", None))[:8]
    for user in suggested_users:
        _attach_profile_fields(user)
        user.followers_count = Follow.objects.filter(following=user).count()

    _attach_profile_fields(request.user)
    unread_notifications = Notification.objects.filter(user=request.user, is_read=False).count()
    unread_messages = Message.objects.filter(receiver=request.user).count()

    return render(
        request,
        "home.html",
        {
            "posts": posts,
            "stories": stories,
            "suggested_users": suggested_users,
            "unread_notifications": unread_notifications,
            "unread_messages": unread_messages,
        },
    )


@login_required
@ensure_csrf_cookie
def reels(request):
    _attach_profile_fields(request.user)
    posts_qs = (
        Post.objects.select_related("user")
        .prefetch_related("likes", "comments")
        .filter(video__isnull=False)
        .exclude(video="")
        .order_by("-created_at")
    )
    for post in posts_qs:
        _attach_profile_fields(post.user)
        _enrich_post(post, request.user)
    return render(request, "reels.html", {"posts": posts_qs})


@csrf_exempt
@login_required
@require_POST
def create_post(request):
    content = request.POST.get("content", "").strip()
    location = request.POST.get("location", "").strip()
    image_files = request.FILES.getlist("image")
    video_files = request.FILES.getlist("video")
    image = image_files[0] if image_files else request.FILES.get("image")
    video = video_files[0] if video_files else request.FILES.get("video")

    # Some clients send video via the same "image" field from a combined picker.
    if image and _is_video_upload(image):
        video = image
        image = None

    if not content and not image and not video:
        return JsonResponse({"error": "Post cannot be empty."}, status=400)

    post = Post.objects.create(
        user=request.user,
        content=content,
        location=location,
        image=image,
        video=video,
    )
    return JsonResponse(
        {
            "id": post.id,
            "username": request.user.username,
            "content": post.content,
            "location": post.location,
            "has_image": bool(post.image),
            "has_video": bool(post.video),
            "created_at": post.created_at.strftime("%b %d, %H:%M"),
        }
    )


@csrf_exempt
@login_required
@require_POST
def create_story_api(request):
    location = request.POST.get("location", "").strip()
    image_files = request.FILES.getlist("image")
    video_files = request.FILES.getlist("video")
    story_files = request.FILES.getlist("story")

    image = image_files[0] if image_files else request.FILES.get("image")
    video = video_files[0] if video_files else request.FILES.get("video")
    media = story_files[0] if story_files else request.FILES.get("story")

    if media and not image and not video:
        if _is_video_upload(media):
            video = media
        else:
            image = media

    if not image and not video:
        return JsonResponse({"error": "Please select an image or video for story."}, status=400)

    story = Story.objects.create(user=request.user, location=location, image=image, video=video)
    media_type = "video" if story.video else "image"
    media_url = _safe_image_url(story.video if story.video else story.image)
    return JsonResponse(
        {
            "ok": True,
            "id": story.id,
            "media_type": media_type,
            "media_url": media_url,
            "location": story.location,
            "created_at": story.created_at.strftime("%b %d, %H:%M"),
        }
    )


@csrf_exempt
@login_required
@require_POST
def create_reel_api(request):
    content = request.POST.get("content", "").strip()
    location = request.POST.get("location", "").strip()
    video_files = request.FILES.getlist("video")
    reel_files = request.FILES.getlist("reel")
    video = (
        (video_files[0] if video_files else None)
        or (reel_files[0] if reel_files else None)
        or request.FILES.get("video")
        or request.FILES.get("reel")
    )
    if not video:
        return JsonResponse({"error": "Please select a reel video."}, status=400)
    if not _is_video_upload(video):
        return JsonResponse({"error": "Please upload a valid video file for reel."}, status=400)

    post = Post.objects.create(user=request.user, content=content, location=location, video=video)
    return JsonResponse(
        {
            "ok": True,
            "id": post.id,
            "location": post.location,
            "created_at": post.created_at.strftime("%b %d, %H:%M"),
        }
    )


@csrf_exempt
@login_required
@require_POST
def like_post(request, post_id):
    post = get_object_or_404(Post, id=post_id)
    like, created = Like.objects.get_or_create(user=request.user, post=post)

    if not created:
        like.delete()
        liked = False
    else:
        liked = True
        if post.user != request.user:
            _notify_user(post.user, f"{request.user.username} liked your post.", actor=request.user)

    likes_count = post.likes.count()
    return JsonResponse({"liked": liked, "total_likes": likes_count, "likes_count": likes_count})


@csrf_exempt
@login_required
@require_POST
def follow_toggle(request, user_id):
    target_user = get_object_or_404(User, id=user_id)

    if target_user == request.user:
        return JsonResponse({"error": "You cannot follow yourself."}, status=400)

    relation, created = Follow.objects.get_or_create(follower=request.user, following=target_user)

    if not created:
        relation.delete()
        following = False
    else:
        following = True
        _notify_user(target_user, f"{request.user.username} started following you.", actor=request.user)

    followers_count = Follow.objects.filter(following=target_user).count()
    return JsonResponse({"following": following, "followers_count": followers_count})


@csrf_exempt
@login_required
@require_POST
def add_comment(request, post_id):
    post = get_object_or_404(Post, id=post_id)

    content = request.POST.get("content", "").strip()
    if not content and request.content_type and "application/json" in request.content_type:
        try:
            payload = json.loads(request.body.decode("utf-8"))
            content = str(payload.get("content", "")).strip()
        except (json.JSONDecodeError, UnicodeDecodeError):
            content = ""

    if not content:
        return JsonResponse({"error": "Comment cannot be empty."}, status=400)

    comment = Comment.objects.create(post=post, user=request.user, text=content)
    if post.user != request.user:
        _notify_user(post.user, f"{request.user.username} commented on your post.", actor=request.user)

    return JsonResponse(
        {
            "ok": True,
            "id": comment.id,
            "content": comment.text,
            "username": request.user.username,
            "created_at": comment.created_at.strftime("%b %d, %H:%M"),
        }
    )


@csrf_exempt
@login_required
@require_POST
def save_post(request, post_id):
    get_object_or_404(Post, id=post_id)

    saved_ids = set(request.session.get("saved_post_ids", []))
    if post_id in saved_ids:
        saved_ids.remove(post_id)
        saved = False
    else:
        saved_ids.add(post_id)
        saved = True

    request.session["saved_post_ids"] = sorted(saved_ids)
    request.session.modified = True
    return JsonResponse({"ok": True, "saved": saved})


@csrf_exempt
@login_required
@require_POST
def delete_post(request, post_id):
    post = get_object_or_404(Post, id=post_id)
    if post.user != request.user:
        return JsonResponse({"error": "You can only delete your own posts."}, status=403)

    post.delete()
    return JsonResponse({"ok": True})


@csrf_exempt
@login_required
@require_POST
def update_post(request, post_id):
    post = get_object_or_404(Post, id=post_id)
    if post.user != request.user:
        return JsonResponse({"error": "You can only edit your own posts."}, status=403)

    content = request.POST.get("content", "").strip()
    location = request.POST.get("location", "").strip()
    if request.content_type and "application/json" in request.content_type:
        try:
            payload = json.loads(request.body.decode("utf-8"))
            if "content" in payload:
                content = str(payload.get("content", "")).strip()
            if "location" in payload:
                location = str(payload.get("location", "")).strip()
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    post.content = content
    post.location = location
    post.save(update_fields=["content", "location"])
    return JsonResponse(
        {
            "ok": True,
            "id": post.id,
            "content": post.content,
            "location": post.location,
        }
    )


@login_required
@require_GET
def story_detail_api(request, story_id):
    story = get_object_or_404(Story.objects.select_related("user"), id=story_id)
    user_stories = Story.objects.filter(user=story.user).order_by("created_at")

    media_type = "video" if story.video else "image"
    media_file = story.video if story.video else story.image
    return JsonResponse(
        {
            "id": story.id,
            "user": {"id": story.user.id, "username": story.user.username},
            "time_ago": f"{timesince(story.created_at)} ago",
            "location": story.location,
            "media_type": media_type,
            "media_url": _safe_image_url(media_file),
            "stories": [{"id": item.id, "viewed": False} for item in user_stories],
        }
    )


@csrf_exempt
@login_required
@require_POST
def story_mark_viewed_api(request, story_id):
    get_object_or_404(Story, id=story_id)
    return JsonResponse({"ok": True})


@csrf_exempt
@login_required
@require_POST
def delete_story_api(request, story_id):
    story = get_object_or_404(Story, id=story_id)
    if story.user != request.user:
        return JsonResponse({"error": "You can only delete your own story."}, status=403)
    story.delete()
    return JsonResponse({"ok": True})


@require_GET
def search(request):
    query = request.GET.get("q", "").strip()
    users = User.objects.none()
    posts = Post.objects.none()
    recent_searches = request.session.get("recent_searches", [])

    if query:
        recent_searches = [query] + [item for item in recent_searches if item != query]
        request.session["recent_searches"] = recent_searches[:10]
        request.session.modified = True

    if query:
        users = User.objects.filter(username__icontains=query)[:20]
        posts = Post.objects.select_related("user").filter(content__icontains=query)[:20]

    for user in users:
        _attach_profile_fields(user)
        user.followers_count = Follow.objects.filter(following=user).count()
        user.posts_count = Post.objects.filter(user=user).count()
        user.is_following = request.user.is_authenticated and Follow.objects.filter(
            follower=request.user,
            following=user,
        ).exists()

    for post in posts:
        _attach_profile_fields(post.user)
        _enrich_post(post, request.user if request.user.is_authenticated else None)

    suggested_users = User.objects.exclude(id=getattr(request.user, "id", None))[:12]
    for user in suggested_users:
        _attach_profile_fields(user)
        user.followers_count = Follow.objects.filter(following=user).count()

    return render(
        request,
        "search.html",
        {
            "query": query,
            "users": users,
            "posts": posts,
            "recent_searches": recent_searches[:5],
            "suggested_users": suggested_users,
            "tags": [],
        },
    )


@require_GET
def search_api(request):
    query = request.GET.get("q", "").strip()
    users = User.objects.filter(username__icontains=query).values("id", "username")[:10]

    payload = [
        {
            "id": user["id"],
            "username": user["username"],
            "is_me": request.user.is_authenticated and user["id"] == request.user.id,
            "is_following": request.user.is_authenticated
            and Follow.objects.filter(follower=request.user, following_id=user["id"]).exists(),
            "can_follow": request.user.is_authenticated and user["id"] != getattr(request.user, "id", None),
        }
        for user in users
    ]
    return JsonResponse({"results": payload})


@login_required
def notifications(request):
    notes_qs = Notification.objects.filter(user=request.user)
    unread_count = notes_qs.filter(is_read=False).count()
    notes = notes_qs[:40]

    return render(
        request,
        "notifications.html",
        {
            "notifications": notes,
            "unread_count": unread_count,
        },
    )


@login_required
@require_GET
def notifications_api(request):
    notes_qs = Notification.objects.filter(user=request.user)
    notes = notes_qs[:20]
    return JsonResponse(
        {
            "unread_count": notes_qs.filter(is_read=False).count(),
            "items": [
                {
                    "id": note.id,
                    "message": note.message,
                    "is_read": note.is_read,
                    "created_at": note.created_at.strftime("%b %d, %H:%M"),
                }
                for note in notes
            ],
        }
    )


@login_required
@require_POST
def notifications_mark_read(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return JsonResponse({"ok": True})


@login_required
def chat(request, user_id):
    user_chat = get_object_or_404(User, id=user_id)
    messages = Message.objects.filter(
        Q(sender=request.user, receiver=user_chat) | Q(sender=user_chat, receiver=request.user)
    )

    contacts = User.objects.exclude(id=request.user.id)[:30]

    return render(
        request,
        "chat.html",
        {
            "messages": messages,
            "user_chat": user_chat,
            "contacts": contacts,
        },
    )


@login_required
def direct_messages(request):
    first_contact = User.objects.exclude(id=request.user.id).first()
    if not first_contact:
        return redirect("home")
    return redirect("chat", user_id=first_contact.id)


@csrf_exempt
@login_required
@require_POST
def api_chat_send(request):
    receiver_id = request.POST.get("receiver_id")
    text = request.POST.get("text", "").strip()

    if (not receiver_id or not text) and request.content_type and "application/json" in request.content_type:
        try:
            payload = json.loads(request.body.decode("utf-8"))
            receiver_id = receiver_id or payload.get("receiver_id")
            text = text or str(payload.get("text", "")).strip()
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    if not receiver_id:
        return JsonResponse({"error": "Receiver is required."}, status=400)
    if not text:
        return JsonResponse({"error": "Message cannot be empty."}, status=400)

    receiver = get_object_or_404(User, id=receiver_id)
    message = Message.objects.create(sender=request.user, receiver=receiver, text=text)
    return JsonResponse(
        {
            "ok": True,
            "message": {
                "id": message.id,
                "sender_id": request.user.id,
                "sender": request.user.username,
                "text": message.text,
                "created_at": message.created_at.strftime("%H:%M"),
            },
        }
    )


@csrf_exempt
@login_required
@require_POST
def api_chat_upload_image(request):
    receiver_id = request.POST.get("receiver_id")
    image = request.FILES.get("image")
    if not receiver_id:
        return JsonResponse({"error": "Receiver is required."}, status=400)
    if not image:
        return JsonResponse({"error": "Please select an image."}, status=400)

    receiver = get_object_or_404(User, id=receiver_id)
    message = Message.objects.create(sender=request.user, receiver=receiver, image=image)
    return JsonResponse(
        {
            "ok": True,
            "message": {
                "id": message.id,
                "sender_id": request.user.id,
                "sender": request.user.username,
                "text": "",
                "image": _safe_image_url(message.image),
                "created_at": message.created_at.strftime("%H:%M"),
            },
        }
    )


@csrf_exempt
@login_required
@require_POST
def api_chat_mark_read(request, user_id):
    return JsonResponse({"ok": True})


@login_required
def profile(request, username):
    profile_user = get_object_or_404(User, username=username)
    user_posts = Post.objects.filter(user=profile_user)
    for post in user_posts:
        _enrich_post(post, request.user if request.user.is_authenticated else None)

    _attach_profile_fields(profile_user)
    if request.user.is_authenticated:
        _attach_profile_fields(request.user)

    followers_count = Follow.objects.filter(following=profile_user).count()
    following_count = Follow.objects.filter(follower=profile_user).count()
    is_following = (
        request.user.is_authenticated
        and Follow.objects.filter(follower=request.user, following=profile_user).exists()
    )
    saved_post_ids = request.session.get("saved_post_ids", []) if request.user.is_authenticated else []
    saved_posts = Post.objects.filter(id__in=saved_post_ids, image__isnull=False).order_by("-created_at")
    for post in saved_posts:
        _enrich_post(post, request.user if request.user.is_authenticated else None)

    return render(
        request,
        "profile.html",
        {
            "profile_user": profile_user,
            "user_posts": user_posts,
            "posts_count": user_posts.count(),
            "followers_count": followers_count,
            "following_count": following_count,
            "is_following": is_following,
            "saved_posts": saved_posts,
        },
    )


@login_required
def edit_profile(request):
    return redirect("profile", username=request.user.username)


@login_required
@require_POST
def profile_update_api(request):
    profile = _ensure_profile(request.user)
    username = request.POST.get("username", "").strip()
    full_name = request.POST.get("full_name", "").strip()
    email = request.POST.get("email", "").strip()
    bio = request.POST.get("bio", "").strip()
    profile_pic = request.FILES.get("profile_pic")

    if username and username != request.user.username:
        if User.objects.filter(username=username).exclude(id=request.user.id).exists():
            return JsonResponse({"error": "Username already taken."}, status=400)
        request.user.username = username

    if full_name:
        parts = full_name.split(" ", 1)
        request.user.first_name = parts[0]
        request.user.last_name = parts[1] if len(parts) > 1 else ""
    else:
        request.user.first_name = ""
        request.user.last_name = ""

    request.user.email = email
    profile.bio = bio
    if profile_pic:
        profile.profile_pic = profile_pic

    try:
        request.user.save()
    except IntegrityError:
        return JsonResponse({"error": "Unable to save profile."}, status=400)
    profile.save()
    _attach_profile_fields(request.user)
    return JsonResponse(
        {
            "ok": True,
            "username": request.user.username,
            "full_name": request.user.get_full_name(),
            "bio": profile.bio,
            "profile_pic": _safe_image_url(profile.profile_pic),
        }
    )


@login_required
@require_GET
def post_detail_api(request, post_id):
    post = get_object_or_404(Post.objects.select_related("user"), id=post_id)
    _attach_profile_fields(post.user)
    _enrich_post(post, request.user)
    return JsonResponse(
        {
            "id": post.id,
            "content": post.content,
            "image": _safe_image_url(post.image),
            "user": {
                "id": post.user.id,
                "username": post.user.username,
                "profile_pic": _safe_image_url(post.user.profile_pic),
            },
            "likes_count": post.likes_count,
            "comments_count": post.comments_count,
            "created_at": post.created_at.strftime("%b %d, %H:%M"),
        }
    )


@login_required
@require_GET
def user_followers_api(request, user_id):
    user = get_object_or_404(User, id=user_id)
    followers = (
        Follow.objects.filter(following=user)
        .select_related("follower")
        .order_by("-created_at")
    )
    data = []
    for item in followers:
        follower = item.follower
        _attach_profile_fields(follower)
        data.append(
            {
                "id": follower.id,
                "username": follower.username,
                "full_name": follower.get_full_name(),
                "profile_pic": _safe_image_url(follower.profile_pic),
                "is_following": Follow.objects.filter(
                    follower=request.user, following=follower
                ).exists(),
            }
        )
    return JsonResponse(data, safe=False)


@login_required
@require_GET
def user_following_api(request, user_id):
    user = get_object_or_404(User, id=user_id)
    following = (
        Follow.objects.filter(follower=user)
        .select_related("following")
        .order_by("-created_at")
    )
    data = []
    for item in following:
        target = item.following
        _attach_profile_fields(target)
        data.append(
            {
                "id": target.id,
                "username": target.username,
                "full_name": target.get_full_name(),
                "profile_pic": _safe_image_url(target.profile_pic),
                "is_following": Follow.objects.filter(
                    follower=request.user, following=target
                ).exists(),
            }
        )
    return JsonResponse(data, safe=False)


@login_required
def user_settings(request):
    return redirect("activity")


@login_required
def user_activity(request):
    time_filter = request.GET.get("time", "30d")
    now = timezone.now()
    days_map = {"7d": 7, "30d": 30, "90d": 90, "1y": 365}
    days = days_map.get(time_filter, 30)
    window_start = now - timedelta(days=days)

    user_posts_qs = Post.objects.filter(user=request.user)
    recent_posts_qs = user_posts_qs.filter(created_at__gte=window_start)
    recent_post_ids = list(recent_posts_qs.values_list("id", flat=True))

    posts_count = Post.objects.filter(user=request.user).count()
    likes_count = Like.objects.filter(user=request.user).count()
    comments_count = Comment.objects.filter(user=request.user).count()
    followers_count = Follow.objects.filter(following=request.user).count()
    following_count = Follow.objects.filter(follower=request.user).count()

    previous_start = window_start - timedelta(days=days)
    prev_posts = user_posts_qs.filter(created_at__gte=previous_start, created_at__lt=window_start).count()
    prev_likes = Like.objects.filter(user=request.user, post__created_at__gte=previous_start, post__created_at__lt=window_start).count()
    prev_comments = Comment.objects.filter(user=request.user, created_at__gte=previous_start, created_at__lt=window_start).count()
    prev_followers = Follow.objects.filter(following=request.user, created_at__gte=previous_start, created_at__lt=window_start).count()
    prev_following = Follow.objects.filter(follower=request.user, created_at__gte=previous_start, created_at__lt=window_start).count()

    def growth(curr, prev):
        if prev == 0:
            return 100 if curr > 0 else 0
        return round(((curr - prev) / prev) * 100)

    posts_growth = growth(recent_posts_qs.count(), prev_posts)
    likes_growth = growth(likes_count, prev_likes)
    comments_growth = growth(comments_count, prev_comments)
    followers_growth = growth(followers_count, prev_followers)
    following_change = abs(growth(following_count, prev_following))

    posts_this_month = user_posts_qs.filter(created_at__gte=now - timedelta(days=30)).count()
    following_this_month = Follow.objects.filter(follower=request.user, created_at__gte=now - timedelta(days=30)).count()
    new_followers = Follow.objects.filter(following=request.user, created_at__gte=now - timedelta(days=7)).count()

    avg_likes_per_post = round(likes_count / posts_count, 1) if posts_count else 0
    avg_comments_per_post = round(comments_count / posts_count, 1) if posts_count else 0

    posts_percentage = min(posts_count * 10, 100)
    likes_percentage = min(int(avg_likes_per_post * 20), 100)
    comments_percentage = min(int(avg_comments_per_post * 25), 100)
    followers_percentage = min(followers_count * 2, 100)
    following_percentage = min(following_count * 2, 100)

    likes_received = Like.objects.filter(post__user=request.user).count()
    comments_received = Comment.objects.filter(post__user=request.user).count()
    saves_count = 0
    shares_count = 0
    profile_views = followers_count * 3 + posts_count * 7
    post_reach = likes_received * 12 + comments_received * 20 + posts_count * 15
    account_discovery = followers_count + following_count + posts_count * 2
    interactions = likes_received + comments_received
    engagement_rate = round((interactions / max(posts_count, 1)) * 10, 1) if posts_count else 0
    if engagement_rate > 100:
        engagement_rate = 100
    engagement_remaining = round(max(0, 100 - engagement_rate), 1)

    chart_labels = []
    chart_posts = []
    chart_likes = []
    chart_comments = []
    for idx in range(6, -1, -1):
        day_start = (now - timedelta(days=idx)).replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        chart_labels.append(day_start.strftime("%b %d"))
        chart_posts.append(user_posts_qs.filter(created_at__gte=day_start, created_at__lt=day_end).count())
        chart_likes.append(Like.objects.filter(post__user=request.user, post__created_at__gte=day_start, post__created_at__lt=day_end).count())
        chart_comments.append(Comment.objects.filter(post__user=request.user, created_at__gte=day_start, created_at__lt=day_end).count())

    top_posts = (
        user_posts_qs.annotate(likes_count=Count("likes"), comments_count=Count("comments"))
        .order_by("-likes_count", "-comments_count", "-created_at")[:8]
    )

    recent_activities = []
    recent_likes = Like.objects.filter(post__user=request.user).select_related("user", "post").order_by("-id")[:5]
    for like in recent_likes:
        recent_activities.append(
            {
                "user": like.user,
                "action": "liked your post",
                "time": "recently",
                "post": like.post,
            }
        )
    recent_comments = Comment.objects.filter(post__user=request.user).select_related("user", "post").order_by("-created_at")[:5]
    for comment in recent_comments:
        recent_activities.append(
            {
                "user": comment.user,
                "action": "commented on your post",
                "time": timesince(comment.created_at) + " ago",
                "post": comment.post,
            }
        )
    recent_activities = recent_activities[:10]

    time_spent = min(180, 15 + posts_count * 4 + comments_count * 2)
    return render(
        request,
        "activity.html",
        {
            "posts_count": posts_count,
            "likes_count": likes_count,
            "comments_count": comments_count,
            "followers_count": followers_count,
            "following_count": following_count,
            "posts_growth": posts_growth,
            "likes_growth": likes_growth,
            "comments_growth": comments_growth,
            "followers_growth": followers_growth,
            "following_change": following_change,
            "posts_percentage": posts_percentage,
            "likes_percentage": likes_percentage,
            "comments_percentage": comments_percentage,
            "followers_percentage": followers_percentage,
            "following_percentage": following_percentage,
            "posts_this_month": posts_this_month,
            "avg_likes_per_post": avg_likes_per_post,
            "avg_comments_per_post": avg_comments_per_post,
            "new_followers": new_followers,
            "following_this_month": following_this_month,
            "engagement_rate": engagement_rate,
            "engagement_remaining": engagement_remaining,
            "profile_views": profile_views,
            "post_reach": post_reach,
            "account_discovery": account_discovery,
            "recent_activities": recent_activities,
            "top_posts": top_posts,
            "likes_received": likes_received,
            "comments_received": comments_received,
            "shares_count": shares_count,
            "saves_count": saves_count,
            "time_spent": time_spent,
            "chart_labels": json.dumps(chart_labels),
            "chart_posts": json.dumps(chart_posts),
            "chart_likes": json.dumps(chart_likes),
            "chart_comments": json.dumps(chart_comments),
        },
    )


@login_required
@require_GET
def activity_stats_api(request):
    posts_count = Post.objects.filter(user=request.user).count()
    likes_received = Like.objects.filter(post__user=request.user).count()
    comments_received = Comment.objects.filter(post__user=request.user).count()
    followers_count = Follow.objects.filter(following=request.user).count()
    return JsonResponse(
        {
            "posts_count": posts_count,
            "likes_received": likes_received,
            "comments_received": comments_received,
            "followers_count": followers_count,
        }
    )


@login_required
def saved_posts(request):
    saved_ids = request.session.get("saved_post_ids", [])
    posts = Post.objects.select_related("user").filter(id__in=saved_ids).order_by("-created_at")
    for post in posts:
        _attach_profile_fields(post.user)
        _enrich_post(post, request.user)
    return render(request, "saved_posts.html", {"posts": posts})


@login_required
def toggle_dark_mode(request):
    request.session["dark_mode"] = not request.session.get("dark_mode", False)
    request.session.modified = True
    return redirect(request.META.get("HTTP_REFERER", "home"))


@login_required
def help_center(request):
    return render(request, "help_center.html")


@login_required
def live(request):
    return render(request, "live.html")
