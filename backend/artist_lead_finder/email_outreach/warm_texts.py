"""Warm-up emails: short personal notes put together from parts, so two emails are rarely
the same. No links, no attachments, no sales words: mail between people who know each
other."""

import random

SUBJECTS = [
    "quick one",
    "this week",
    "that session",
    "re the mix",
    "sounds good",
    "tomorrow?",
    "the new track",
    "studio time",
    "catching up",
    "saw your message",
    "about friday",
    "small update",
    "listened to it",
    "notes from today",
    "the demo",
    "next steps",
    "hey",
    "thanks again",
    "running late",
    "that playlist",
]
OPENERS = [
    "Hey {name},",
    "Yo {name},",
    "Hi {name},",
    "{name},",
    "Hey,",
    "Morning {name},",
]
LINES = [
    "Finally got around to listening to the new version. The drums hit way harder now.",
    "Are we still good for the session this week? I can move it if you need.",
    "I bounced a rough mix last night, the low end still needs some work.",
    "Been thinking about the hook, maybe we try it half time on the second verse.",
    "Thanks for sending that over, I'll go through it tonight.",
    "The vocals sound clean, just a bit loud in the chorus.",
    "Quick heads up, I'm out of town Thursday so let's lock something for next week.",
    "Did you end up talking to the engineer about the master?",
    "I added a couple of ideas to the bridge, let me know what you think.",
    "Coffee this weekend? Want to show you a few beats I've been working on.",
    "That reference track you sent is exactly the vibe.",
    "I'm going to redo the intro, it feels too long right now.",
    "Got the files, everything opened fine on my end.",
    "Let's keep the tempo where it is, it feels right.",
    "I played it in the car this morning and it sounds great on speakers.",
    "Can you send me the stems when you get a chance? No rush.",
    "Studio is booked from 2 to 6, come through whenever.",
    "I think the second take was better honestly.",
    "Still working on the cover art, should have something by Friday.",
    "Sorry for the slow reply, it's been a crazy week.",
]
CLOSERS = [
    "Talk soon",
    "Let me know",
    "Catch you later",
    "Thanks",
    "Speak soon",
    "Cheers",
    "Later",
    "",
]
REPLIES = [
    "Sounds good, thanks!",
    "Got it, I'll take a look tonight.",
    "Yeah that works for me.",
    "Appreciate it, will get back to you tomorrow.",
    "Love that idea, let's try it.",
    "Perfect, see you then.",
    "Haha true. Let's do it.",
    "Thanks for the heads up.",
    "Makes sense. I'll send the files later today.",
    "Cool, I'm around all week.",
    "Agreed, the second one is better.",
    "Nice, can't wait to hear it.",
    "No worries at all, take your time.",
    "Bet, talk soon.",
    "Just listened, it's sounding really good.",
    "Works for me, I'll block the time.",
]


def first_name(address: str) -> str:
    local = address.split("@", 1)[0]
    word = "".join(ch for ch in local.split(".")[0] if ch.isalpha())
    return word.capitalize() if 2 <= len(word) <= 12 else ""


def note(rng: random.Random, to_address: str) -> tuple[str, str]:
    """(subject, body) of a first warm-up email."""
    name = first_name(to_address)
    opener = rng.choice(OPENERS if name else [item for item in OPENERS if "{name}" not in item])
    lines = rng.sample(LINES, rng.choice((1, 2, 2, 3)))
    closer = rng.choice(CLOSERS)
    body = opener.format(name=name) + "\n\n" + " ".join(lines)
    if closer:
        body += f"\n\n{closer}"
    return rng.choice(SUBJECTS), body


def reply(rng: random.Random) -> str:
    return rng.choice(REPLIES)
