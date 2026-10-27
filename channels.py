"""Meshtastic channel configuration.

The radio MeshPacket contains an 8-bit channel hash, not the
local channel index. Channel configuration is therefore looked
up by hash.
"""

import base64
import binascii


class ChannelConfig:
    """Configuration of one Meshtastic channel."""

    def __init__(self, name, psk, channel_hash=None):
        self.name = name
        self.psk = self._decode_psk(psk)

        if channel_hash is None:
            self.channel_hash = self.generate_hash()
        else:
            self.channel_hash = int(channel_hash) & 0xFF

    @staticmethod
    def _decode_psk(value):
        """Decode PSK supplied as bytes, Base64 or hexadecimal."""

        if isinstance(value, bytes):
            return value

        if not isinstance(value, str):
            raise TypeError("PSK must be bytes or string")

        value = value.strip()

        if not value:
            return b""

        # Try Base64.
        try:
            return base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error):
            pass

        # Try hexadecimal.
        try:
            return bytes.fromhex(value)
        except ValueError:
            raise ValueError(
                "Channel PSK must be valid Base64 or hexadecimal text"
            )

    def generate_hash(self):
        """Calculate the Meshtastic channel hash."""

        name_bytes = self.name.encode("utf-8")

        result = 0

        for value in name_bytes:
            result ^= value

        for value in self.psk:
            result ^= value

        return result & 0xFF

    @property
    def configured(self):
        """Return True when a PSK is configured."""
        return self.psk is not None

    def __repr__(self):
        return (
            f"ChannelConfig("
            f"name={self.name!r}, "
            f"hash=0x{self.channel_hash:02X}, "
            f"psk={'configured' if self.configured else 'not configured'}"
            f")"
        )


class ChannelDB:
    """Collection of manually configured Meshtastic channels."""

    def __init__(self):
        self._channels = {}

    def add(self, channel):
        """Add a channel configuration."""

        if not isinstance(channel, ChannelConfig):
            raise TypeError(
                "channel must be a ChannelConfig instance"
            )

        self._channels[channel.channel_hash] = channel

    def get(self, channel_hash):
        """Find a channel by its 8-bit radio channel hash."""

        return self._channels.get(int(channel_hash) & 0xFF)

    def has(self, channel_hash):
        """Return True if a channel hash is configured."""

        return self.get(channel_hash) is not None

    def remove(self, channel_hash):
        """Remove a channel by hash."""

        self._channels.pop(int(channel_hash) & 0xFF, None)

    def all(self):
        """Return all configured channels."""

        return dict(self._channels)

    def __contains__(self, channel_hash):
        return self.has(channel_hash)

    def __len__(self):
        return len(self._channels)