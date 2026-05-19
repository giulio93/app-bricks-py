# SPDX-FileCopyrightText: Copyright (C) ARDUINO SRL (http://www.arduino.cc)
#
# SPDX-License-Identifier: MPL-2.0

import os
import numpy as np

from .base_speaker import BaseSpeaker, FormatPlain, FormatPacked


class Speaker:
    """
    Unified Speaker class that can be configured for different speaker types.

    Device routing is managed by the host orchestrator via the AUDIO_DEVICE or
    PIPEWIRE_PROPS environment variables. The default device ('default') routes
    audio through PipeWire's ALSA compatibility layer.

    Note: constructor arguments (except those in signature) must be provided in
    keyword format to forward them correctly to the specific speaker implementations.
    """

    # =============================================================================
    # Predefined devices
    # =============================================================================
    DEFAULT = "default"
    """Routes audio through PipeWire's ALSA compat layer. Sink selection via PIPEWIRE_PROPS."""
    USB_SPEAKER_1 = "usb:1"
    """Shorthand for the first USB speaker available."""
    USB_SPEAKER_2 = "usb:2"
    """Shorthand for the second USB speaker available."""

    # =============================================================================
    # Sample Rate Constants
    # =============================================================================

    RATE_8K = 8000
    """8 kHz - Telephony bandwidth, VoIP applications"""

    RATE_16K = 16000
    """16 kHz - Common for voice processing, speech recognition, keyword spotting"""

    RATE_32K = 32000
    """32 kHz - Higher quality voice, some broadcast applications"""

    RATE_44K = 44100
    """44.1 kHz - CD quality audio standard"""

    RATE_48K = 48000
    """48 kHz - Professional audio, broadcast standard, high-quality recording"""

    # =============================================================================
    # Channel Constants
    # =============================================================================

    CHANNELS_MONO = 1
    """Mono - Single channel audio"""

    CHANNELS_STEREO = 2
    """Stereo - Two channel audio (left and right)"""

    # =============================================================================
    # Buffer Size Constants
    # =============================================================================

    BUFFER_SIZE_REALTIME = 256
    """
    Real-time/low-latency: ~16ms @ 16kHz, ~5ms @ 48kHz

    Use for: Live audio effects, real-time playback, low-latency voice chat
    Trade-off: Low latency but high CPU usage
    """

    BUFFER_SIZE_BALANCED = 1024
    """
    Balanced (default): ~64ms @ 16kHz, ~21ms @ 48kHz

    Use for: Voice commands, keyword spotting, general audio processing
    Trade-off: Good balance between latency and efficiency
    """

    BUFFER_SIZE_SAFE = 4096
    """
    Transcription/recording: ~256ms @ 16kHz, ~85ms @ 48kHz

    Use for: Speech recognition, transcription, recording, non-real-time processing
    Trade-off: High latency but low CPU usage
    """

    def __new__(
        cls,
        device: str = "",
        sample_rate: int = RATE_16K,
        channels: int = CHANNELS_MONO,
        format: FormatPlain | FormatPacked = np.int16,
        buffer_size: int = BUFFER_SIZE_BALANCED,
        **kwargs,
    ) -> BaseSpeaker:
        """
        Create a speaker instance.

        Args:
            device (Union[str, int]): Speaker device identifier. Supports:
                - Empty string / omitted: auto-detect (AUDIO_DEVICE env → USB → PipeWire default)
                - int | str digit: ALSA card index (e.g., 0, 1)
                - str: ALSA device name (e.g., "plughw:CARD=MyCard,DEV=0")
                - str: device file path (e.g., "/dev/snd/by-id/usb-My-Device-00")
                - str: Speaker.USB_SPEAKER_x macros
                - str: Speaker.DEFAULT for explicit PipeWire routing
            sample_rate (int): Sample rate in Hz. Default: 16000.
            channels (int): Number of audio channels. Default: 1.
            format (FormatPlain | FormatPacked): Audio format. Default: np.int16.
            buffer_size (int): Size of the audio buffer. Default: 1024.
            **kwargs: Passed to ALSASpeaker (e.g. shared, auto_reconnect).

        Returns:
            BaseSpeaker: Appropriate speaker implementation instance

        Raises:
            SpeakerConfigError: If parameters are invalid

        Examples:
            ```python
            speaker = Speaker()                    # auto: USB if present, else PipeWire
            speaker = Speaker(Speaker.USB_SPEAKER_1)  # force first USB speaker
            speaker = Speaker(Speaker.DEFAULT)     # force PipeWire default routing
            speaker = Speaker("CARD=USB,DEV=0")    # explicit ALSA card name
            ```
        """
        from .alsa_speaker import ALSASpeaker  # Imported here to avoid circular dependency

        return ALSASpeaker(
            device=device,
            sample_rate=sample_rate,
            channels=channels,
            format=format,
            buffer_size=buffer_size,
            **kwargs,
        )

    @staticmethod
    def play_pcm(
        pcm_audio: np.ndarray,
        sample_rate: int,
        channels: int,
        format: FormatPlain | FormatPacked,
        device: str = "",
    ):
        """
        Play raw PCM audio data.

        Args:
            pcm_audio (np.ndarray): Raw PCM audio data in ALSA PCM format.
            sample_rate (int): Sample rate in Hz.
            channels (int): Number of audio channels.
            format (FormatPlain | FormatPacked): Audio format.
            device (str): ALSA device name. Empty string uses AUDIO_DEVICE env or 'default'.

        Raises:
            SpeakerOpenError: If speaker can't be opened.
            SpeakerWriteError: If speaker is not started.
            ValueError: If pcm_audio is empty or invalid.
            Exception: If the underlying implementation fails to write a frame.
        """
        with Speaker(device=device, sample_rate=sample_rate, channels=channels, format=format) as speaker:
            speaker.play_pcm(pcm_audio)

    @staticmethod
    def play_wav(wav_audio: np.ndarray, device: str = ""):
        """
        Play audio from WAV format data.
        Note: Only uncompressed PCM WAV files are supported.

        Args:
            wav_audio (np.ndarray): WAV format audio data (including header).
            device (str): ALSA device name. Empty string uses AUDIO_DEVICE env or 'default'.

        Raises:
            SpeakerOpenError: If speaker can't be opened.
            SpeakerWriteError: If speaker is not started.
            ValueError: If wav_audio is empty or invalid.
            Exception: If the underlying implementation fails to write a frame.
        """
        import io
        import wave

        # Read WAV from numpy array
        wav_channels = 1
        wav_sampwidth = 2
        wav_framerate = 16000
        buffer = io.BytesIO(wav_audio.tobytes())
        try:
            with wave.open(buffer, "rb") as wav_file:
                wav_channels = wav_file.getnchannels()
                wav_sampwidth = wav_file.getsampwidth()
                wav_framerate = wav_file.getframerate()
        except wave.Error as e:
            raise ValueError(f"Invalid WAV data: {e}")

        # We only force integer types due to wave module limitations that
        # only support PCM formats, which are int-based.
        match wav_sampwidth:
            case 1:
                format = "u1"  # 8-bit PCM is unsigned
            case 2:
                format = "<i2"  # 16-bit PCM is signed
            case 3:
                format = ("<i4", True)  # 24-bit PCM packed in 32-bit container
            case 4:
                format = "<i4"  # 32-bit PCM is signed
            case _:
                raise ValueError(f"Unsupported WAV sample width: {wav_sampwidth} bytes")

        # Initialize speaker with WAV file parameters
        with Speaker(device=device, sample_rate=wav_framerate, channels=wav_channels, format=format) as speaker:
            speaker.play_wav(wav_audio)
