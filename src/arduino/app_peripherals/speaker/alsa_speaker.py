# SPDX-FileCopyrightText: Copyright (C) ARDUINO SRL (http://www.arduino.cc)
#
# SPDX-License-Identifier: MPL-2.0

import os
import re
import time
from pathlib import Path
from typing import Optional

import alsaaudio
import numpy as np

from .base_speaker import BaseSpeaker, FormatPlain, FormatPacked
from .errors import SpeakerOpenError, SpeakerWriteError, SpeakerConfigError
from arduino.app_utils.logger import Logger

logger = Logger("ALSASpeaker")

_PIPEWIRE_DEVICE = "default"


class ALSASpeaker(BaseSpeaker):
    """
    ALSA speaker implementation with automatic USB / PipeWire routing.

    Device selection priority:
      1. Explicit ``device`` argument
      2. ``AUDIO_DEVICE`` environment variable (set by host orchestrator)
      3. Auto-detect: first available USB speaker (ALSA path)
      4. Fallback: ALSA ``default`` device (PipeWire routing via PIPEWIRE_PROPS)
    """

    from .speaker import Speaker

    def __init__(
        self,
        device: str | int = "",
        sample_rate: int = Speaker.RATE_16K,
        channels: int = Speaker.CHANNELS_MONO,
        format: FormatPlain | FormatPacked = np.int16,
        buffer_size: int = Speaker.BUFFER_SIZE_BALANCED,
        shared: bool = True,
        auto_reconnect: bool = True,
    ):
        """
        Initialize ALSA speaker.

        Args:
            device (Union[str, int]): Speaker device identifier. Can be:
                - Empty string / omitted: auto-detect (USB first, then PipeWire default)
                - int | str digit: ALSA card index (e.g., 0, 1)
                - str: ALSA device name (e.g., "plughw:CARD=MyCard,DEV=0")
                - str: device file path (e.g., "/dev/snd/by-id/usb-My-Device-00")
                - str: Speaker.USB_SPEAKER_x macros
                - str: Speaker.DEFAULT for explicit PipeWire routing
            sample_rate (int): Sample rate in Hz. Default: 16000.
            channels (int): Number of audio channels. Default: 1 (mono).
            format (FormatPlain | FormatPacked): Audio format. Default: np.int16.
            buffer_size (int): ALSA periodsize. Default: 1024.
            shared (bool): ALSA device sharing mode (USB path only). Default: True.
            auto_reconnect (bool): Retry on failure. Default: True.

        Raises:
            SpeakerConfigError: If the device cannot be resolved or format is unsupported.
        """
        super().__init__(sample_rate, channels, format, buffer_size, auto_reconnect)

        try:
            resolved_identifier = self._pick_device(device)
            if self._is_alsa_resolvable(resolved_identifier):
                self.device_stable_ref = self._resolve_stable_ref(resolved_identifier)
            else:
                self.device_stable_ref = str(resolved_identifier)  # raw device (PipeWire)
            self.name = self._resolve_name(self.device_stable_ref)
        except Exception as e:
            raise SpeakerConfigError(f"Failed to look for speaker device '{device}': {e}")

        self.shared = shared
        self.logger = logger

        self._pcm: Optional[alsaaudio.PCM] = None
        self._last_reconnection_attempt = 0.0

    # ------------------------------------------------------------------
    # Device selection
    # ------------------------------------------------------------------

    @staticmethod
    def _pick_device(device: str | int) -> str | int:
        """
        Resolve the effective device identifier applying the priority chain:
        explicit arg → AUDIO_DEVICE env → USB auto-detect → PipeWire default.
        """
        if device != "" and device is not None:
            return device

        env_device = os.environ.get("AUDIO_DEVICE", "")
        if env_device:
            return env_device

        usb_devices = ALSASpeaker.list_usb_devices()
        if usb_devices:
            return "usb:1"

        return _PIPEWIRE_DEVICE

    @property
    def is_pipewire(self) -> bool:
        """True when the device is opened directly without ALSA card resolution (PipeWire path)."""
        return not self._is_alsa_resolvable(self.device_stable_ref)

    @staticmethod
    def _is_alsa_resolvable(identifier: str | int) -> bool:
        """Return True if the identifier should go through ALSA card resolution."""
        if isinstance(identifier, int):
            return True
        if isinstance(identifier, str):
            if identifier.isdigit():
                return True
            if identifier.startswith("usb:"):
                return True
            if identifier.startswith("/dev/snd/"):
                return True
            if re.match(r"^(plughw:|hw:)", identifier):
                return True
            if re.match(r"^(.+:)?CARD=", identifier):
                return True
            if re.match(r"^(.+:)?(\d+),(\d+)$", identifier):
                return True
        return False  # unknown format → raw PipeWire device

    # ------------------------------------------------------------------
    # Device enumeration (USB / ALSA path)
    # ------------------------------------------------------------------

    @staticmethod
    def list_devices() -> list:
        """Return available ALSA plughw speakers."""
        devices = []
        try:
            for dev in alsaaudio.pcms(alsaaudio.PCM_PLAYBACK):
                if dev.startswith("plughw:CARD="):
                    devices.append(dev.removeprefix("plughw:"))
        except Exception as e:
            logger.error(f"Error retrieving ALSA devices: {e}")
        return devices

    @staticmethod
    def list_usb_devices() -> list:
        """Return available USB ALSA speakers."""
        usb_devices = []
        try:
            cards = alsaaudio.cards()
            card_indexes = alsaaudio.card_indexes()
            card_map = {name: idx for idx, name in zip(card_indexes, cards)}
            for card_name, card_index in card_map.items():
                device_path = Path(f"/sys/class/sound/card{card_index}/device")
                if not device_path.exists():
                    continue
                try:
                    real_path = device_path.resolve()
                    if "usb" in str(real_path).lower():
                        for dev in alsaaudio.pcms(alsaaudio.PCM_PLAYBACK):
                            if dev.startswith("plughw:CARD=") and f"CARD={card_name}," in dev:
                                usb_devices.append(dev.removeprefix("plughw:"))
                except Exception as e:
                    logger.error(f"Error parsing card info for {card_name}: {e}")
        except Exception as e:
            logger.error(f"Error listing USB speakers: {e}")
        return usb_devices

    # ------------------------------------------------------------------
    # Device resolution (USB / ALSA path)
    # ------------------------------------------------------------------

    def _resolve_stable_ref(self, identifier: str | int) -> str:
        """
        Resolve a speaker identifier to a stable ALSA name (e.g. ``CARD=MyCard,DEV=0``).
        Raises RuntimeError if the device cannot be found.
        """
        if identifier == _PIPEWIRE_DEVICE:
            return _PIPEWIRE_DEVICE

        all_devices = self.list_devices()
        if not all_devices:
            raise RuntimeError("No ALSA speakers found")

        resolved_device = ""
        if isinstance(identifier, str) and not identifier.isdigit():
            raw_hw_match = re.match(r"^(plughw:|hw:)[^,]+,\d+,\d+$", identifier)
            if raw_hw_match:
                return identifier

            if identifier.startswith("usb:"):
                usb_index = int(identifier.removeprefix("usb:")) - 1
                usb_devices = self.list_usb_devices()
                if not usb_devices:
                    raise RuntimeError("No USB speakers found")
                if usb_index < 0 or usb_index >= len(usb_devices):
                    raise RuntimeError(f"USB speaker index {usb_index + 1} out of range. Available: 1-{len(usb_devices)}")
                resolved_device = usb_devices[usb_index]

            elif identifier.startswith("/dev/snd/by-id"):
                if not os.path.exists(identifier):
                    raise RuntimeError(f"{identifier} does not exist")
                device_path = os.path.realpath(identifier)
                base_name = os.path.basename(device_path)
                if base_name.startswith("controlC") and base_name[8:].isdigit():
                    card_idx = int(base_name[8:])
                    card_name = self._resolve_name(card_idx)
                    resolved_device = f"CARD={card_name},DEV=0"

            else:
                numeric_format_match = re.match(r"^(.+:)?(\d+),(\d+)$", identifier)
                if numeric_format_match:
                    try:
                        card_idx = int(numeric_format_match.group(2))
                        device_index = int(numeric_format_match.group(3))
                        card_name = self._resolve_name(card_idx)
                        resolved_device = f"CARD={card_name},DEV={device_index}"
                    except Exception as e:
                        raise RuntimeError(f"Failed to resolve card name for {identifier}: {e}")

                card_name_format_match = re.match(r"^(.+:)?CARD=([^,]+),DEV=(\d+)$", identifier)
                if card_name_format_match:
                    if card_name_format_match.group(1) is not None:
                        resolved_device = identifier.split(":", 1)[-1]
                    else:
                        resolved_device = identifier

        elif isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
            card_idx = int(identifier)
            card_name = self._resolve_name(card_idx)
            resolved_device = f"CARD={card_name},DEV=0"

        if resolved_device:
            if resolved_device not in all_devices:
                raise RuntimeError(f"Resolved device '{resolved_device}' not found among available ALSA devices")
            return resolved_device

        raise RuntimeError(f"Unsupported device identifier: {identifier}")

    def _resolve_runtime_ref(self, device_stable_ref: str) -> tuple[int, int]:
        """Resolve stable ALSA name to (card_index, device_index)."""
        card_indexes = alsaaudio.card_indexes()
        if not card_indexes:
            raise RuntimeError("No ALSA sound cards found")

        match = re.match(r"^(.+:)?CARD=([^,]+),DEV=(\d+)$", device_stable_ref)
        if match:
            try:
                card_name = match.group(2)
                device_index = int(match.group(3))
                for card_idx, curr_card_name in zip(card_indexes, alsaaudio.cards()):
                    if curr_card_name == card_name:
                        return card_idx, device_index
            except Exception as e:
                raise RuntimeError(f"Failed to resolve runtime ref from {device_stable_ref}: {e}")

        raise RuntimeError(f"Invalid device reference: {device_stable_ref}")

    def _resolve_name(self, device_ref: str | int) -> str:
        """Return a human-readable name for the speaker."""
        if not self._is_alsa_resolvable(device_ref):
            return str(device_ref)  # raw PipeWire device — use as-is

        if isinstance(device_ref, str):
            match = re.match(r"^(?:plughw:|hw:)([^,]+),\d+,\d+$", device_ref)
            if match:
                return match.group(1)
            match = re.match(r"^(.+:)?CARD=([^,]+),DEV=(\d+)$", device_ref)
            if match:
                try:
                    return match.group(2)
                except Exception as e:
                    raise RuntimeError(f"Failed to resolve name from {device_ref}: {e}")

        elif isinstance(device_ref, int):
            cards = alsaaudio.cards()
            if device_ref < 0 or device_ref >= len(cards):
                raise RuntimeError(f"Card index {device_ref} out of range. Available: 0-{len(cards) - 1}")
            return cards[device_ref]

        raise RuntimeError(f"Invalid device reference: {device_ref} (type:{type(device_ref)})")

    # ------------------------------------------------------------------
    # PCM open / close / write
    # ------------------------------------------------------------------

    @property
    def alsa_format_idx(self) -> int:
        return getattr(alsaaudio, "PCM_FORMAT_" + self.alsa_format_name)

    @property
    def alsa_format_name(self) -> str:
        return _dtype_to_alsa_format_name(self.format, self.format_is_packed)

    def _open_speaker(self) -> None:
        logger.debug(f"Opening PCM device: {self.device_stable_ref}")

        try:
            if self.is_pipewire:
                device = _PIPEWIRE_DEVICE
            elif self.shared:
                card_idx, device_idx = self._resolve_runtime_ref(self.device_stable_ref)
                device = f"plug_card_{card_idx}_dev_{device_idx}_spk"
            else:
                raw_hw_match = re.match(r"^(plughw:|hw:)[^,]+,\d+,\d+$", self.device_stable_ref)
                if raw_hw_match:
                    device = self.device_stable_ref
                else:
                    card_idx, device_idx = self._resolve_runtime_ref(self.device_stable_ref)
                    device = f"plughw:CARD={card_idx},DEV={device_idx}"

            self._pcm = alsaaudio.PCM(
                type=alsaaudio.PCM_PLAYBACK,
                mode=alsaaudio.PCM_NORMAL,
                device=device,
                rate=self.sample_rate,
                channels=self.channels,
                format=self.alsa_format_idx,
                periodsize=self.buffer_size,
            )

            info = self._pcm.info()

            actual_rate = info["rate"]
            if self.sample_rate != actual_rate:
                logger.warning(f"Requested sample rate {self.sample_rate}Hz not supported by {device}. Using {actual_rate}Hz instead.")
                self.sample_rate = actual_rate

            actual_channels = info["channels"]
            if self.channels != actual_channels:
                logger.warning(f"Requested channels {self.channels} not supported by {device}. Using {actual_channels} instead.")
                self.channels = actual_channels

            actual_format_name = info["format_name"]
            if self.alsa_format_idx != info["format"]:
                logger.warning(f"Requested format {self.alsa_format_name} not supported by {device}. Using {actual_format_name} instead.")
                self.format = _alsa_format_name_to_dtype(actual_format_name)

            actual_buffer_size = info["period_size"]
            if self.buffer_size != actual_buffer_size:
                logger.warning(f"Requested buffer_size {self.buffer_size} not supported by {device}. Using {actual_buffer_size} instead.")
                self.buffer_size = actual_buffer_size

        except SpeakerOpenError:
            raise

        except alsaaudio.ALSAAudioError as e:
            if "busy" in str(e):
                raise SpeakerOpenError(f"Speaker is busy. Close other audio applications and try again. ({self.device_stable_ref})")
            else:
                raise RuntimeError(f"ALSA error opening speaker: {e}")

        except Exception as e:
            raise RuntimeError(f"Unexpected error opening speaker: {e}")

        logger.debug(f"PCM opened: {device}, {self.sample_rate}Hz, {self.channels}ch, {self.format}, {self.buffer_size} frames/IO")

    def _close_speaker(self) -> None:
        if self._pcm is not None:
            try:
                self._pcm.close()
            except Exception as e:
                logger.warning(f"Error closing PCM device: {e}")
            finally:
                self._pcm = None

    def _write_audio(self, audio_chunk: np.ndarray):
        try:
            if self._pcm is None:
                if not self.auto_reconnect:
                    return None

                current_time = time.monotonic()
                elapsed = current_time - self._last_reconnection_attempt
                if elapsed < self.auto_reconnect_delay:
                    time.sleep(self.auto_reconnect_delay - elapsed)
                self._last_reconnection_attempt = current_time

                self._open_speaker()
                self.logger.info(f"Successfully reopened speaker {self.name}")

            result = self._pcm.write(audio_chunk.tobytes())
            if result < 0:
                logger.debug(f"PCM write returned error code: {'EPIPE' if result == -32 else result}")
                self._pcm.write(audio_chunk.tobytes())

        except (alsaaudio.ALSAAudioError, SpeakerOpenError, SpeakerWriteError, Exception) as e:
            if self._is_device_disconnected():
                self.logger.error(
                    f"Failed to write to speaker {self.name}: {e}."
                    f"{' Retrying...' if self.auto_reconnect else ' Auto-reconnect is disabled, please restart the app.'}"
                )
                self._close_speaker()
                return
            self.logger.error(f"Unexpected error writing audio chunk: {e}")

    def _is_device_disconnected(self) -> bool:
        if self.is_pipewire:
            return False  # PipeWire default is always present
        try:
            return self.device_stable_ref not in self.list_devices()
        except Exception as e:
            logger.debug(f"Error checking device status: {e}")
            return True


def _dtype_to_alsa_format_name(dtype: np.dtype, is_packed: bool = False) -> str:
    """Map numpy dtype to ALSA PCM format string."""
    kind = dtype.kind
    size = dtype.itemsize
    byteorder = dtype.byteorder

    if byteorder == "=" or byteorder == "|":
        import sys
        byteorder = "<" if sys.byteorder == "little" else ">"

    if kind == "i":
        if size == 1:
            return "S8"
        elif size == 2:
            return "S16_LE" if byteorder == "<" else "S16_BE"
        elif size == 4:
            if is_packed:
                return "S24_LE" if byteorder == "<" else "S24_BE"
            return "S32_LE" if byteorder == "<" else "S32_BE"

    elif kind == "u":
        if size == 1:
            return "U8"
        elif size == 2:
            return "U16_LE" if byteorder == "<" else "U16_BE"
        elif size == 4:
            return "U32_LE" if byteorder == "<" else "U32_BE"

    elif kind == "f":
        if size == 4:
            return "FLOAT_LE" if byteorder == "<" else "FLOAT_BE"
        elif size == 8:
            return "FLOAT64_LE" if byteorder == "<" else "FLOAT64_BE"

    raise SpeakerConfigError(f"Unsupported numpy dtype for ALSA: {dtype}")


def _alsa_format_name_to_dtype(alsa_format: str) -> np.dtype:
    """Map ALSA PCM format string to numpy dtype."""
    format_map = {
        "S8": "int8",
        "U8": "uint8",
        "S16_LE": "<i2",
        "S16_BE": ">i2",
        "U16_LE": "<u2",
        "U16_BE": ">u2",
        "S24_LE": "<i4",
        "S24_BE": ">i4",
        "S32_LE": "<i4",
        "S32_BE": ">i4",
        "U32_LE": "<u4",
        "U32_BE": ">u4",
        "FLOAT_LE": "<f4",
        "FLOAT_BE": ">f4",
        "FLOAT64_LE": "<f8",
        "FLOAT64_BE": ">f8",
    }

    dtype_str = format_map.get(alsa_format)
    if dtype_str is None:
        raise SpeakerOpenError(f"Unsupported conversion for ALSA format to numpy dtype: {alsa_format}")

    return np.dtype(dtype_str)
