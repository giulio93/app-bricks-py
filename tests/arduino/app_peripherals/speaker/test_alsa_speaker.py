# SPDX-FileCopyrightText: Copyright (C) ARDUINO SRL (http://www.arduino.cc)
#
# SPDX-License-Identifier: MPL-2.0

import os
import pytest
from unittest.mock import patch

import alsaaudio
import numpy as np

from arduino.app_peripherals.speaker.speaker import Speaker
from arduino.app_peripherals.speaker.alsa_speaker import ALSASpeaker, _alsa_format_name_to_dtype, _dtype_to_alsa_format_name
from arduino.app_peripherals.speaker.errors import SpeakerConfigError, SpeakerOpenError


class TestALSASpeakerInitialization:
    """Test ALSA speaker initialization."""

    def test_alsa_start_opens_device(self, mock_alsa_usb_speakers, pcm_registry):
        """Test that start() opens ALSA device."""
        spkr = Speaker(device=0)

        assert not spkr.is_started()
        spkr.start()
        assert spkr.is_started()
        assert pcm_registry.get_last_instance() is not None

    def test_alsa_stop_closes_device(self, mock_alsa_usb_speakers, pcm_registry):
        """Test that stop() closes ALSA device."""
        spkr = Speaker(device=0)
        spkr.start()
        spkr.stop()

        assert not spkr.is_started()
        assert pcm_registry.get_last_instance().close.called


class TestALSASpeakerDeviceSelection:
    """Test device selection priority chain."""

    def test_auto_picks_usb_when_available(self, mock_alsa_usb_speakers):
        """Auto-detect selects first USB speaker when USB is present."""
        spkr = ALSASpeaker()
        assert spkr.device_stable_ref == "CARD=SomeCard,DEV=0"
        assert not spkr.is_pipewire

    def test_auto_falls_back_to_pipewire_when_no_usb(self):
        """Auto-detect falls back to PipeWire default when no USB speakers found."""
        with patch("arduino.app_peripherals.speaker.alsa_speaker.alsaaudio.pcms", return_value=[]):
            spkr = ALSASpeaker()
        assert spkr.is_pipewire
        assert spkr.device_stable_ref == "default"

    def test_audio_device_env_var_takes_priority(self, mock_alsa_usb_speakers):
        """AUDIO_DEVICE env var is used before USB auto-detect."""
        with patch.dict(os.environ, {"AUDIO_DEVICE": "pipewire_node_from_orchestrator"}):
            spkr = ALSASpeaker()
        assert spkr.device_stable_ref == "pipewire_node_from_orchestrator"

    def test_explicit_arg_overrides_env_var(self, mock_alsa_usb_speakers):
        """Explicit device arg wins over AUDIO_DEVICE env var."""
        with patch.dict(os.environ, {"AUDIO_DEVICE": "env_node"}):
            spkr = ALSASpeaker(device=Speaker.USB_SPEAKER_1)
        assert spkr.device_stable_ref == "CARD=SomeCard,DEV=0"

    def test_explicit_pipewire_default(self):
        """Speaker.DEFAULT forces PipeWire routing regardless of USB availability."""
        spkr = ALSASpeaker(device=Speaker.DEFAULT)
        assert spkr.is_pipewire
        assert spkr.device_stable_ref == "default"


class TestALSASpeakerDeviceResolution:
    """Test ALSA device resolution (USB path)."""

    def test_resolve_by_usb_shorthand(self, mock_alsa_usb_speakers):
        spkr = ALSASpeaker(device=Speaker.USB_SPEAKER_1)
        assert spkr.device_stable_ref == "CARD=SomeCard,DEV=0"

        spkr = ALSASpeaker(device=Speaker.USB_SPEAKER_2)
        assert spkr.device_stable_ref == "CARD=AnotherCard,DEV=0"

    def test_resolve_by_integer_index(self, mock_alsa_usb_speakers):
        spkr = ALSASpeaker(device=0)
        assert spkr.device_stable_ref == "CARD=SomeCard,DEV=0"

        spkr = ALSASpeaker(device=1)
        assert spkr.device_stable_ref == "CARD=AnotherCard,DEV=0"

    def test_resolve_explicit_device_name(self, mock_alsa_usb_speakers):
        spkr = ALSASpeaker(device="CARD=SomeCard,DEV=0")
        assert spkr.device_stable_ref == "CARD=SomeCard,DEV=0"

        spkr = ALSASpeaker(device="plughw:CARD=SomeCard,DEV=0")
        assert spkr.device_stable_ref == "CARD=SomeCard,DEV=0"

    @patch("arduino.app_peripherals.speaker.alsa_speaker.alsaaudio.pcms", return_value=[])
    def test_resolve_no_alsa_devices_raises_error(self, mock_pcms, mock_alsa_usb_speakers):
        with pytest.raises(SpeakerConfigError) as exc_info:
            ALSASpeaker(device=0)
        assert "No ALSA speakers found" in str(exc_info.value)

    def test_resolve_out_of_range_raises_error(self, mock_alsa_usb_speakers):
        with pytest.raises(SpeakerConfigError) as exc_info:
            ALSASpeaker(device=5)
        assert "out of range" in str(exc_info.value)


class TestALSAErrorManagement:
    """Test handling ALSA errors."""

    def test_device_busy_error(self, mock_alsa_usb_speakers):
        spkr = ALSASpeaker(device="CARD=SomeCard,DEV=0")
        spkr.auto_reconnect_delay = 0

        with patch(
            "arduino.app_peripherals.speaker.alsa_speaker.alsaaudio.PCM",
            side_effect=alsaaudio.ALSAAudioError("Device or resource busy"),
        ):
            with pytest.raises(SpeakerOpenError) as exc_info:
                spkr.start()
        assert "busy" in str(exc_info.value).lower()

    def test_generic_alsa_error(self, mock_alsa_usb_speakers):
        """Base class retries then raises SpeakerOpenError on persistent failure."""
        spkr = ALSASpeaker(device="CARD=SomeCard,DEV=0")
        spkr.auto_reconnect_delay = 0

        with patch(
            "arduino.app_peripherals.speaker.alsa_speaker.alsaaudio.PCM",
            side_effect=alsaaudio.ALSAAudioError("Some generic ALSA error"),
        ):
            with pytest.raises(SpeakerOpenError):
                spkr.start()

    def test_write_error_doesnt_raise(self, mock_alsa_usb_speakers, pcm_registry):
        spkr = ALSASpeaker(device="CARD=SomeCard,DEV=0")
        spkr.start()

        pcm_instance = pcm_registry.get_last_instance()
        pcm_instance.write = lambda data: -32  # EPIPE

        audio_data = np.zeros(1024, dtype=np.int16)
        spkr.play(audio_data)  # Should not raise

    def test_stop_with_close_error(self, mock_alsa_usb_speakers, pcm_registry):
        spkr = ALSASpeaker(device="CARD=SomeCard,DEV=0")
        spkr.start()

        pcm_instance = pcm_registry.get_last_instance()
        pcm_instance.close.side_effect = alsaaudio.ALSAAudioError("Close failed")

        spkr.stop()
        assert not spkr.is_started()


class TestALSADeviceDisconnection:
    """Test ALSA device disconnection handling."""

    def test_detect_device_disconnection(self, mock_alsa_usb_speakers, pcm_registry):
        spkr = ALSASpeaker()
        spkr.start()

        pcm_instance = pcm_registry.get_last_instance()
        pcm_instance.write = lambda data: None

        with patch("arduino.app_peripherals.speaker.alsa_speaker.alsaaudio.pcms", return_value=[]):
            audio_data = np.zeros(1024, dtype=np.int16)
            spkr.play(audio_data)
            assert spkr._pcm is None

    def test_list_devices_check(self, mock_alsa_usb_speakers):
        devices = ALSASpeaker.list_devices()
        assert len(devices) > 0

        with patch("arduino.app_peripherals.speaker.alsa_speaker.alsaaudio.pcms", return_value=[]):
            devices = ALSASpeaker.list_devices()
            assert len(devices) == 0

    def test_pipewire_device_never_disconnected(self):
        """PipeWire default is always considered connected."""
        spkr = ALSASpeaker(device=Speaker.DEFAULT)
        assert spkr._is_device_disconnected() is False


class TestALSADeviceReconnection:
    """Test ALSA device reconnection logic."""

    def test_reconnection_after_device_available(self, mock_alsa_usb_speakers):
        with patch("arduino.app_peripherals.speaker.alsa_speaker.alsaaudio.pcms", return_value=[]):
            with pytest.raises(SpeakerConfigError):
                ALSASpeaker(device="CARD=SomeCard,DEV=0")

        spkr = ALSASpeaker(device="CARD=SomeCard,DEV=0")
        spkr.start()
        assert spkr.is_started()
        spkr.stop()

    def test_write_reconnects(self, mock_alsa_usb_speakers, pcm_registry):
        spkr = ALSASpeaker()
        spkr.start()

        pcm_instance = pcm_registry.get_last_instance()
        pcm_instance.write = lambda data: None

        audio_data = np.zeros(1024, dtype=np.int16)
        spkr.play(audio_data)

        pcm_instance.write = lambda data: len(data)
        spkr.play(audio_data)  # Should handle gracefully


class TestALSAPlayback:
    """Test ALSA speaker playback methods."""

    def test_alsa_speaker_play(self, mock_alsa_usb_speakers):
        spkr = ALSASpeaker()
        spkr.start()

        audio_data = np.zeros(1024, dtype=np.int16)
        spkr.play(audio_data)

    @pytest.mark.parametrize(
        "format",
        [np.uint8, np.uint16, np.uint32, np.int8, np.int16, np.int32, np.float32, np.float64],
    )
    def test_alsa_has_correct_format(self, mock_alsa_usb_speakers, pcm_registry, format):
        format_dtype = np.dtype(format)

        spkr = ALSASpeaker(format=format, buffer_size=128)
        spkr.start()

        audio_data = np.zeros(128, dtype=format_dtype)
        spkr.play(audio_data)

        pcm_instance = pcm_registry.get_last_instance()
        assert format_dtype == _alsa_format_name_to_dtype(spkr.alsa_format_name)
        assert spkr.alsa_format_name == _dtype_to_alsa_format_name(format_dtype)
        assert spkr.alsa_format_idx == pcm_instance.info()["format"]
        assert spkr.alsa_format_name == pcm_instance.info()["format_name"]

    def test_unsupported_format_with_none_dtype(self):
        with pytest.raises(SpeakerConfigError):
            ALSASpeaker(format=None)

        with pytest.raises(SpeakerConfigError):
            ALSASpeaker(format="unsupported_format")


class TestALSAVolumeControl:
    """Test ALSA speaker volume control."""

    def test_volume_default(self, mock_alsa_usb_speakers):
        spkr = ALSASpeaker()
        assert spkr.volume == 100

    def test_volume_setter(self, mock_alsa_usb_speakers):
        spkr = ALSASpeaker()
        spkr.volume = 50
        assert spkr.volume == 50

    def test_volume_out_of_range(self, mock_alsa_usb_speakers):
        spkr = ALSASpeaker()
        with pytest.raises(ValueError):
            spkr.volume = -1
        with pytest.raises(ValueError):
            spkr.volume = 101

    def test_volume_affects_output(self, mock_alsa_usb_speakers, pcm_registry):
        spkr = ALSASpeaker()
        spkr.start()
        spkr.volume = 50

        audio_data = np.full(1024, 1000, dtype=np.int16)
        spkr.play(audio_data)


class TestALSASharedMode:
    """Test ALSA speaker shared mode."""

    def test_shared_mode_default(self, mock_alsa_usb_speakers):
        spkr = ALSASpeaker()
        assert spkr.shared is True

    def test_exclusive_mode(self, mock_alsa_usb_speakers):
        spkr = ALSASpeaker(shared=False)
        assert spkr.shared is False
        spkr.start()
        assert spkr.is_started()
