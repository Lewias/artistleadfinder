"""Word lists of the local classifier. Phrases are written in normalised form
(lowercase, hyphens as spaces); see text.normalize_text."""

import re

ARTIST_STRONG = [
    "recording artist",
    "music artist",
    "rap artist",
    "hip hop artist",
    "independent artist",
    "indie artist",
    "rapper",
    "singer",
    "songwriter",
    "singer songwriter",
    "musician",
    "vocalist",
    "performer",
    "рэпер",
    "репер",
    "певец",
    "певица",
    "музыкант",
    "исполнитель",
    "автор песен",
]
ARTIST_MEDIUM = ["dj", "band", "rap group", "music group", "группа"]
# "artist" alone may be a visual artist: small weight only.
ARTIST_WEAK = ["artist", "артист"]

RELEASE_PHRASES = [
    "my new single",
    "my new song",
    "my album",
    "my ep",
    "new single",
    "new album",
    "new ep",
    "out now",
    "streaming now",
    "music video",
    "official video",
    "listen now",
    "available now",
    "all platforms",
    "stream now",
    "pre save",
    "presave",
    "tour dates",
    "новый сингл",
    "новый альбом",
    "новый трек",
    "уже на всех площадках",
    "слушайте на всех площадках",
]
RELEASE_WEAK = ["new music", "new song", "coming soon", "новая музыка"]
# First-person ownership + release: "my new single", "our debut album", "мой новый трек".
FIRST_PERSON_RELEASE = re.compile(
    r"(?<![\w])(?:my|our|мой|мой новый|наш|наш новый|моя|моя новая|мой дебютный)\s+"
    r"(?:(?:new|debut|latest|first|next|brand new)\s+)?"
    r"(?:single|song|album|ep|mixtape|track|record|music video|video|project|"
    r"сингл|трек|альбом|клип|песня)(?![\w])"
)

# Streaming / smart-link services of artists. YouTube is weak on its own.
MUSIC_LINK_HOSTS = {
    "spotify.com": "Spotify",
    "open.spotify.com": "Spotify",
    "music.apple.com": "Apple Music",
    "soundcloud.com": "SoundCloud",
    "on.soundcloud.com": "SoundCloud",
    "bandcamp.com": "Bandcamp",
    "audiomack.com": "Audiomack",
    "album.link": "album.link",
    "song.link": "song.link",
    "lnk.to": "lnk.to",
    "ffm.to": "ffm.to",
    "hyperfollow.com": "HyperFollow",
    "distrokid.com": "DistroKid",
    "unitedmasters.com": "UnitedMasters",
    "tunecore.com": "TuneCore",
    "cdbaby.com": "CD Baby",
    "music.yandex.ru": "Яндекс Музыка",
    "band.link": "band.link",
}
WEAK_MUSIC_LINK_HOSTS = {
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
    "m.youtube.com": "YouTube",
}
BEAT_MARKETPLACE_HOSTS = {
    "beatstars.com": "BeatStars",
    "bsta.rs": "BeatStars",
    "airbit.com": "Airbit",
    "traktrain.com": "Traktrain",
    "soundee.com": "Soundee",
}

PRODUCER_STRONG = [
    "music producer",
    "record producer",
    "beatmaker",
    "beat maker",
    "prod by me",
    "produced by me",
    "beats for sale",
    "type beats",
    "type beat",
    "битмейкер",
    "саунд продюсер",
    "музыкальный продюсер",
]
# "producer" alone: film/TV/event producers exist, so it needs music context for full weight.
PRODUCER_AMBIGUOUS = ["producer", "продюсер"]
PRODUCER_MEDIUM = [
    "beats",
    "composer",
    "drumkit",
    "drum kit",
    "loopkit",
    "loop kit",
    "sample pack",
    "midi kit",
    "sound kit",
    "soundkit",
    "биты",
    "композитор",
]
PRODUCER_CONTEXTUAL = ["production", "продакшн"]
BEATS_WORDING = re.compile(r"(?<![\w])(?:beats?|prod|prod\.|type beats?|биты?)(?![\w])")

MEDIA_STRONG = [
    "music news",
    "hip hop news",
    "rap news",
    "music blog",
    "music magazine",
    "music press",
    "music radio",
    "repost hub",
    "repost page",
    "playlist curator",
    "promo page",
    "music promo",
    "record label",
    "музыкальные новости",
    "музыкальный блог",
    "рэп новости",
    "лейбл",
]
# Need music context: generic radio / podcast / label pages are not music leads by themselves.
MEDIA_MEDIUM = ["radio", "podcast", "curator", "label", "playlist", "editorial", "подкаст"]

MUSIC_SERVICES = [
    "videographer",
    "video editor",
    "video director",
    "music video director",
    "cinematographer",
    "filmmaker",
    "photographer",
    "audio engineer",
    "sound engineer",
    "recording engineer",
    "mix engineer",
    "mixing engineer",
    "mastering engineer",
    "mixing",
    "mastering",
    "recording studio",
    "music studio",
    "звукорежиссер",
    "звукорежиссёр",
    "сведение",
    "мастеринг",
    "студия звукозаписи",
    "клипмейкер",
    "видеограф",
    "фотограф",
]

NEGATIVE_STRONG = [
    "forex",
    "crypto trader",
    "crypto trading",
    "casino",
    "betting",
    "sports betting",
    "onlyfans",
    "dropshipping",
    "spam",
    "giveaway",
    "backup account",
    "backup acc",
    "fan page",
    "fanpage",
    "fan account",
    "meme page",
    "memes",
]
NEGATIVE_MEDIUM = [
    "nails",
    "beauty salon",
    "boutique",
    "realtor",
    "real estate",
    "car sales",
    "visual artist",
    "tattoo artist",
    "makeup artist",
    "make up artist",
    "nail artist",
    "digital artist",
    "wedding",
    "weddings",
]

# Around music, "media" words and creative services count; without it they do not.
MUSIC_CONTEXT = re.compile(
    r"(?<![\w])(?:music|musical|hip hop|hiphop|rap|r&b|rnb|trap|drill|afrobeats?|beats?|"
    r"artists|musicians?|rappers?|singers?|songwriters?|vocalists?|dj|"
    r"songs?|albums?|singles?|mixtapes?|record label|"
    r"музык\w*|рэп\w*|хип хоп|трек\w*|артист(?:ы|ов|ам|ами))(?![\w])"
)
# Music emojis in a bio ("#Artist 🎤🎶") are music context too.
MUSIC_EMOJI = re.compile("[🎤🎙🎧🎶🎵🎼🎹🎸🎷🎺🥁🎚🎛💿📀]")

INSTAGRAM_CATEGORIES = {
    "musician/band": "artist",
    "musician": "artist",
    "band": "artist",
    "singer": "artist",
    "rapper": "artist",
    "dj": "artist",
    "music producer": "producer",
    "music production studio": "producer",
    "record label": "media",
    "media/news company": "media",
    "news & media website": "media",
    "radio station": "media",
    "music chart": "media",
    "podcast": "media",
    # Russian interface labels of the same categories.
    "музыкант/группа": "artist",
    "музыкант": "artist",
    "исполнитель": "artist",
    "музыкальный продюсер": "producer",
    "звукозаписывающая компания": "media",
    "радиостанция": "media",
}
# Categories that only hint: "Artist" may be a visual artist, "Producer" a film producer.
INSTAGRAM_WEAK_CATEGORIES = {
    "artist": "artist",
    "producer": "producer",
    "деятель искусств": "artist",
    "продюсер": "producer",
}

# Substrings of the username; they only add score, never decide alone.
USERNAME_PATTERNS = {
    "producer": ["prodby", "prod", "producer", "beats", "beatmaker", "typebeats", "composer"],
    "artist": ["music", "mp3", "rapper", "singer"],
    "media": ["news", "radio", "blog", "magazine", "podcast", "promo", "records"],
}


def phrase_pattern(phrases: list[str]) -> re.Pattern:
    """Longest phrase first, so "music producer" wins over "producer" at one position."""
    escaped = "|".join(re.escape(p) for p in sorted(set(phrases), key=len, reverse=True))
    return re.compile(rf"(?<![\w])(?:{escaped})(?![\w])")
