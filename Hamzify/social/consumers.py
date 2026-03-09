import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.contrib.auth.models import AnonymousUser, User

from .models import Message


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get("user")
        if not self.user or isinstance(self.user, AnonymousUser):
            await self.close()
            return

        self.other_user_id = int(self.scope["url_route"]["kwargs"]["user_id"])
        users = sorted([self.user.id, self.other_user_id])
        self.room_group_name = f"chat_{users[0]}_{users[1]}"

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data):
        payload = json.loads(text_data)
        text = payload.get("text", "").strip()
        if not text:
            return

        receiver = await self._get_user(self.other_user_id)
        message = await self._save_message(self.user, receiver, text)

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat.message",
                "text": message.text,
                "sender": self.user.username,
                "sender_id": self.user.id,
                "created_at": message.created_at.strftime("%b %d, %H:%M"),
            },
        )

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event))

    @database_sync_to_async
    def _get_user(self, user_id):
        return User.objects.get(id=user_id)

    @database_sync_to_async
    def _save_message(self, sender, receiver, text):
        return Message.objects.create(sender=sender, receiver=receiver, text=text)


class NotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get("user")
        if not self.user or isinstance(self.user, AnonymousUser):
            await self.close()
            return

        self.group_name = f"notify_{self.user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def notify_message(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    "message": event["message"],
                    "created_at": event["created_at"],
                    "actor": event.get("actor"),
                }
            )
        )
