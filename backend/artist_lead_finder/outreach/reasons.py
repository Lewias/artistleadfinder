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
# Follow-up chains: why a run stopped, paused or waits (schema 28).
REPLY_RECEIVED = "REPLY_RECEIVED"
CONVERSATION_STOPPED = "CONVERSATION_STOPPED"
USER_STOPPED = "USER_STOPPED"
USER_PAUSED = "USER_PAUSED"
# Planned before follow-ups were sent for real: waits until the user starts it.
LEGACY = "LEGACY"
# The thread does not show our previous message: not the dialog the chain belongs to.
THREAD_MISMATCH = "THREAD_MISMATCH"
# No confirmed previous message to follow up on.
ORIGIN_MISSING = "ORIGIN_MISSING"
UNCONFIRMED = "UNCONFIRMED"
SENDER_MISMATCH = "SENDER_MISMATCH"

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
    REPLY_RECEIVED,
    CONVERSATION_STOPPED,
    USER_STOPPED,
    USER_PAUSED,
    LEGACY,
    THREAD_MISMATCH,
    ORIGIN_MISSING,
    UNCONFIRMED,
    SENDER_MISMATCH,
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
