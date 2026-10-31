"""Meshtastic PKI cryptography helpers."""
import base64
import json
import hashlib
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESCCM
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

def expand_channel_psk(psk):
    """
    Expand a Meshtastic channel PSK to an AES key.

    Supported forms:
        0 bytes       -> no encryption
        1 byte 0x00   -> no encryption
        1 byte 0x01   -> default PSK
        1 byte 0x02.. -> default PSK with last byte incremented
        16 bytes      -> AES-128
        32 bytes      -> AES-256
    """

    psk = bytes(psk)

    if len(psk) == 0:
        return None

    if len(psk) == 1:
        index = psk[0]

        if index == 0:
            return None

        default_psk = bytes([
            0xD4, 0xF1, 0xBB, 0x3A,
            0x20, 0x29, 0x07, 0x59,
            0xF0, 0xBC, 0xFF, 0xAB,
            0xCF, 0x4E, 0x69, 0x01,
        ])

        key = bytearray(default_psk)
        key[-1] = (key[-1] + index - 1) & 0xFF

        return bytes(key)

    if len(psk) == 16:
        return psk

    if len(psk) == 32:
        return psk

    raise ValueError(
        "Meshtastic channel PSK must contain "
        "0, 1, 16 or 32 bytes"
    )


def decrypt_channel(raw_mesh_packet, psk):
    """
    Decrypt a Meshtastic channel-encrypted packet.

    Channel encryption uses AES-CTR. There is no authentication tag.
    """

    raw = bytes(raw_mesh_packet)

    if len(raw) < 17:
        raise ValueError("MeshPacket is too short")

    packet_id = int.from_bytes(raw[8:12], "little")
    from_node = int.from_bytes(raw[4:8], "little")

    encrypted = raw[16:-2]

    key = expand_channel_psk(psk)

    if key is None:
        return encrypted

    nonce = (
        packet_id.to_bytes(8, "little")
        + from_node.to_bytes(4, "little")
        # + bytes(8)
        + b"\x00\x00\x00\x00"
    )

    cipher = Cipher(
        algorithms.AES(key),
        modes.CTR(nonce),
    )

    decryptor = cipher.decryptor()

    return decryptor.update(encrypted) + decryptor.finalize()


def load_private_key(value):
    """Load a 32-byte X25519 private key from Base64 or hexadecimal text."""
    if not value:
        raise RuntimeError("Set MESHTASTIC_SENDER_PRIVATE_KEY to an X25519 private key (Base64 or hex).")
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:
        raw = bytes.fromhex(value)
    if len(raw) != 32:
        raise ValueError("X25519 private key must be exactly 32 bytes.")
    return raw


def derive_pki_key(private_key_bytes, recipient_public_key_b64):
    pub = base64.b64decode(recipient_public_key_b64)
    shared = X25519PrivateKey.from_private_bytes(private_key_bytes).exchange(
        X25519PublicKey.from_public_bytes(pub)
    )
    return hashlib.sha256(shared).digest()


def decrypt_pki(raw_mesh_packet, key):
    """Decrypt a Meshtastic PKI packet. Returns parsed crypto fields and plaintext."""
    raw = bytes(raw_mesh_packet)
    if len(raw) < 28:
        raise ValueError("MeshPacket is too short for PKI payload")

    to = int.from_bytes(raw[0:4], "little")
    fr = int.from_bytes(raw[4:8], "little")
    packet_id = int.from_bytes(raw[8:12], "little")
    flags, channel, next_hop, relay = raw[12:16]
    enc = raw[16:-2]
    if len(enc) < 12:
        raise ValueError("Encrypted payload is too short")

    ct = enc[:-12]
    tag = enc[-12:-4]
    extra = enc[-4:]
    nonce = raw[8:12] + extra + raw[4:8] + b"\x00"
    pt = AESCCM(key, tag_length=8).decrypt(nonce, ct + tag, None)
    return {
        "raw": raw,
        "to": to,
        "from": fr,
        "pid": packet_id,
        "flags": flags,
        "channel": channel,
        "next_hop": next_hop,
        "relay": relay,
        "ct": ct,
        "tag": tag,
        "extra": extra,
        "pt": pt,
    }


def decrypt_packet(raw_mesh_packet, channels=None, pki_key=None):
    from protocol import parse_mesh_packet, validate_data_proto

    raw = bytes(raw_mesh_packet)
    packet = parse_mesh_packet(raw)

    if packet is None:
        print("FAIL: parse_mesh_packet() returned None")
        return {
            "mode": "unsupported",
            "plaintext": None,
            "packet": None,
        }

    # ------------------------------------------------------------
    # PKI
    # ------------------------------------------------------------

    if pki_key is not None:
        print("Trying PKI...")

        try:
            result = decrypt_pki(raw, pki_key)
            plaintext = result["pt"]

            print("PKI decrypt succeeded")
            print("plaintext:", plaintext.hex())

            if validate_data_proto(plaintext):
                print("Data protobuf: VALID")

                return {
                    "mode": "pki",
                    "plaintext": plaintext,
                    "packet": packet,
                    "crypto": result,
                }

            print("Data protobuf: INVALID")

        except Exception as e:
            print("PKI decrypt failed:", repr(e))

    else:
        print("PKI skipped: no key")

        # ------------------------------------------------------------
    # CHANNEL: configured key, then default PSK
    # ------------------------------------------------------------

    channel = None

    if channels is not None:
        channel = channels.get(packet["channel"])

        print("Channel lookup:", channel)

        if channel is not None:
            try:
                plaintext = decrypt_channel(
                    raw,
                    channel.psk,
                )

                if validate_data_proto(plaintext):
                    print("Channel Data protobuf: VALID")

                    return {
                        "mode": "channel",
                        "plaintext": plaintext,
                        "packet": packet,
                        "channel": channel,
                        "channel_name": channel.name,
                    }

                print("Channel Data protobuf: INVALID")

            except Exception as e:
                print("Channel decrypt failed:", repr(e))

    # ------------------------------------------------------------
    # FALLBACK: Meshtastic default PSK (AQ==)
    # ------------------------------------------------------------

    print("Trying default PSK (AQ==)...")

    try:
        default_psk = b"\x01"

        plaintext = decrypt_channel(
            raw,
            default_psk,
        )

        if validate_data_proto(plaintext):
            print("Default PSK Data protobuf: VALID")

            return {
                "mode": "channel",
                "plaintext": plaintext,
                "packet": packet,
                "channel": channel,
                "channel_name": (
                    channel.name
                    if channel is not None
                    else "Default PSK"
                ),
                "used_default_psk": True,
            }

        print("Default PSK Data protobuf: INVALID")

    except Exception as e:
        print("Default PSK decrypt failed:", repr(e))

    return {
        "mode": "unsupported",
        "plaintext": None,
        "packet": packet,
    }
    
    
def parse_node_id(value):
    """Convert a Meshtastic Node ID to an integer."""
    if isinstance(value, int):
        return value

    value = str(value).strip()

    if value.startswith("!"):
        value = value[1:]

    return int(value, 16)


def load_pki_peers(value):
    """Load and validate peer public keys and node names."""

    if not value:
        return {}

    if not isinstance(value, dict):
        raise ValueError("PKI peers must be a dictionary")

    peers = {}

    for node_id, peer in value.items():
        node_id = parse_node_id(node_id)

        if not isinstance(peer, dict):
            raise ValueError(
                f"Invalid peer settings for node {node_id:08X}"
            )

        public_key = peer.get("public_key", "")

        if not isinstance(public_key, str) or not public_key:
            raise ValueError(
                f"Missing public key for node {node_id:08X}"
            )

        pub = base64.b64decode(public_key, validate=True)

        if len(pub) != 32:
            raise ValueError(
                f"Public key for {node_id:08X} must be 32 bytes"
            )

        peers[node_id] = {
            "public_key": public_key,
            "short_name": str(peer.get("short_name", "")).strip(),
            "long_name": str(peer.get("long_name", "")).strip(),
        }

    return peers


def derive_packet_pki_key(
    private_key_bytes,
    packet,
    own_node_id,
    peers,
):
    """
    Derive a PKI key for a packet addressed to or sent by our node.

    The private key must belong to own_node_id.
    The peer public key is selected from the other packet endpoint.
    """
    own_node_id = parse_node_id(own_node_id)

    to = packet["to"]
    fr = packet["from"]

    if fr == own_node_id:
        peer_id = to
    elif to == own_node_id:
        peer_id = fr
    else:
        return None

    peer = peers.get(peer_id)

    if peer is None:
        return None

    public_key = peer["public_key"]

    return derive_pki_key(
        private_key_bytes,
        public_key,
    )