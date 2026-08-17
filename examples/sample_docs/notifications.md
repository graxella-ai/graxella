# Notifications API — inline deprecation form

The `send_email` tool is deprecated. Use `send_notification` for all new
integrations; it multiplexes email, Slack, and SMS transports.

## send_notification(recipient: str, body: str, channel: str = "email") -> str

Sends a notification via the requested channel.

Intent: notify_user
