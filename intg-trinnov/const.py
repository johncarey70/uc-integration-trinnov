"""
Defines constant enumerations used for Trinnov remote and media player control.

Includes:
- `SimpleCommands`: Enum mapping human-readable command names to Trinnov-specific remote commands.
  Covers numeric inputs, aspect ratio changes, navigation, power control, and more.
- Designed for use with `ucapi`-based entity integration modules (e.g., remote, media_player).

These constants provide a unified interface for issuing commands across UC integrations.
"""


from enum import Enum

from ucapi import media_player, remote


class EntityPrefix(str, Enum):
    """Enumeration of supported entities"""
    MEDIA_PLAYER = "media_player"
    REMOTE = "remote"
    SAMPLE_RATE = "sample_rate"
    AUDIO_SYNC = "audio_sync"
    VOLUME = "volume"
    MUTED = "muted"


class SimpleCommands(str, Enum):
    """Enumeration of supported remote command names for Trinnov control."""

    BACK = "back"
    BYPASS_OFF = "bypass_off"
    BYPASS_ON = "bypass_on"
    BYPASS_TOGGLE = "bypass_toggle"
    DIM_OFF = "dim_off"
    DIM_ON = "dim_on"
    DIM_TOGGLE = "dim_toggle"
    FAV_LIGHT = "fav_light"
    MUTE_OFF = "mute_off"
    MUTE_ON = "mute_on"
    MUTE_TOGGLE = "mute_toggle"
    SELECT_SOUND_MODE = "select_sound_mode"
    SELECT_SOURCE = "select_source"
    VOLUME = "volume"
    VOLUME_DOWN = "volume_down"
    VOLUME_UP = "volume_up"

    @property
    def display_name(self) -> str:
        """
        Returns the display-friendly command name for use in UI or command APIs.
        Capitalizes only the first word; keeps remaining words as-is lowercase.

        :return: A display-safe string.
        """
        parts = self.name.replace("_", " ").lower().split(maxsplit=1)
        return parts[0].capitalize() + (f" {parts[1]}" if len(parts) > 1 else "")

class MediaPlayerDef: # pylint: disable=too-few-public-methods
    """
    Defines a media player entity including supported features, attributes, and
    a list of simple commands.
    """
    features = [
        media_player.Features.MUTE,
        media_player.Features.MUTE_TOGGLE,
        media_player.Features.ON_OFF,
        media_player.Features.SELECT_SOUND_MODE,
        media_player.Features.SELECT_SOURCE,
        media_player.Features.UNMUTE,
        media_player.Features.VOLUME,
        media_player.Features.VOLUME_UP_DOWN,
    ]
    attributes = {
        media_player.Attributes.MUTED: False,
        media_player.Attributes.SOUND_MODE: "",
        media_player.Attributes.SOUND_MODE_LIST: [],
        media_player.Attributes.SOURCE: "",
        media_player.Attributes.SOURCE_LIST: [],
        media_player.Attributes.STATE: media_player.States.OFF,
        media_player.Attributes.VOLUME: -20,
    }


class RemoteDef: # pylint: disable=too-few-public-methods
    """
    Defines a remote entity including supported features, attributes, and
    a list of simple commands.
    """
    features = [
        remote.Features.ON_OFF,
        remote.Features.SEND_CMD,
    ]
    attributes = {
        remote.Attributes.STATE: remote.States.OFF
    }
    simple_commands = [cmd.display_name for cmd in SimpleCommands]
