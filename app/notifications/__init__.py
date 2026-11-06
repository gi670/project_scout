"""Notification integrations for GitHub Scout."""

from app.notifications.service import DiscordNotificationService, NotificationError

__all__ = ["DiscordNotificationService", "NotificationError"]
