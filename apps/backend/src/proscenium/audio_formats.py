"""Allowlisted audio content types and exact FFmpeg demuxers."""

AUDIO_DEMUXERS: dict[str, tuple[str, frozenset[str] | None]] = {
    "audio/flac": ("flac", frozenset({"flac"})),
    "audio/wav": ("wav", None),  # WAVE must contain PCM.
    "audio/mpeg": ("mp3", frozenset({"mp3"})),
    "audio/ogg": ("ogg", frozenset({"vorbis", "opus", "flac"})),
}
