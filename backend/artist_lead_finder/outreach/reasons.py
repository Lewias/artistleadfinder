"""Structured skip / failure reasons of outreach recipients and jobs."""

RECIPIENT_UNAVAILABLE = "RECIPIENT_UNAVAILABLE"
ALREADY_CONTACTED = "ALREADY_CONTACTED"
DO_NOT_CONTACT = "DO_NOT_CONTACT"
SENDER_UNAVAILABLE = "SENDER_UNAVAILABLE"
AUTH_REQUIRED = "AUTH_REQUIRED"
CHECKPOINT = "CHECKPOINT"
RATE_LIMITED = "RATE_LIMITED"
MESSAGE_REJECTED = "MESSAGE_REJECTED"
# The profile offers only «Follow»: its owner does not take messages from this account.
MESSAGES_CLOSED = "MESSAGES_CLOSED"
# The thread already had messages: the person is already in this account's Direct.
ALREADY_IN_DIRECT = "ALREADY_IN_DIRECT"
NETWORK_ERROR = "NETWORK_ERROR"
SEND_ERROR = "SEND_ERROR"
CAMPAIGN_CANCELLED = "CAMPAIGN_CANCELLED"

REASONS = (
    RECIPIENT_UNAVAILABLE,
    ALREADY_CONTACTED,
    DO_NOT_CONTACT,
    SENDER_UNAVAILABLE,
    AUTH_REQUIRED,
    CHECKPOINT,
    RATE_LIMITED,
    MESSAGE_REJECTED,
    MESSAGES_CLOSED,
    ALREADY_IN_DIRECT,
    NETWORK_ERROR,
    SEND_ERROR,
    CAMPAIGN_CANCELLED,
)

# Sender problems: the message was not sent and the recipient is not at fault, so the
# job waits for the sender (never retried around the problem, never moved to another
# account).
SENDER_REASONS = {AUTH_REQUIRED, CHECKPOINT, RATE_LIMITED, SENDER_UNAVAILABLE}
SENDER_STATUS_OF = {
    AUTH_REQUIRED: "auth_required",
    CHECKPOINT: "checkpoint",
    RATE_LIMITED: "rate_limited",
}
