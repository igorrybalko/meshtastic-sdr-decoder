import numpy as np

from config import BROADCAST_TO, NODE_ROLES
from app_settings import load_settings
from channels import ChannelConfig

from crypto import (
    load_private_key,
    load_pki_peers,
    parse_node_id,
    decrypt_packet,
    derive_packet_pki_key,
    decrypt_pki,
)

from phy import decode_block, gray_raw_symbol, source_map

from protocol import (
    format_data,
    decode_position,
    PORTNUM_NAMES,
    POSITION_APP,
    parse_mesh_packet,
    classify_packet,
    debug_protobuf_fields,
)

from payload_search import build_spectra, candidate_block, make_plain

from lora_header.whitening import generate_whitening_bytes

KEEP = 5_000


def load_channels_from_settings() -> dict:
    """Load channels from application settings, indexed by channel hash."""
    settings = load_settings()
    channels = {}

    for row_number, item in enumerate(settings.get("channels", []), start=1):
        try:
            name = str(item.get("name", "")).strip()
            if not name:
                raise ValueError("channel name is required")

            # Use the original ChannelConfig implementation:
            # it decodes the PSK and calculates the Meshtastic channel hash.
            psk = item.get("psk") or ""
            channel = ChannelConfig(name=name, psk=psk)

            channels[channel.channel_hash] = channel

        except (TypeError, ValueError) as exc:
            print(
                f"Skipping invalid channel settings row "
                f"{row_number}: {exc}"
            )
    
    return channels


channels = load_channels_from_settings()


def load_pki_settings():
    """Load local node PKI credentials and peers from application settings."""
    settings = load_settings()
    pki = settings.get("pki", {})

    own_node_id_text = pki.get("own_node_id", "").strip()
    private_key_text = pki.get("private_key", "").strip()

    own_node_id = (
        parse_node_id(own_node_id_text)
        if own_node_id_text
        else None
    )

    priv = (
        load_private_key(private_key_text)
        if private_key_text
        else None
    )

    peers = load_pki_peers(pki.get("peers", {}))

    return priv, own_node_id, peers


def initial_nibbles_from_to(to, white):
    to_bytes = to.to_bytes(4, "little")

    whitened = bytes(
        b ^ int(w)
        for b, w in zip(to_bytes, white[:4])
    )

    nibbles = []

    for b in whitened[:2]:
        nibbles.append(b & 0x0F)
        nibbles.append(b >> 4)

    return nibbles


def print_encrypted_packet(mh, packet_length):
    print("\n=== ENCRYPTED MESHTASTIC PACKET ===")
    print("to:", f"!{mh['to']:08x}")
    print("from:", f"!{mh['from']:08x}")
    print("packet id:", f"0x{mh['packet_id']:08X}")
    print("hop limit:", mh["hop_limit"])
    print("hop start:", mh["hop_start"])
    print("want ack:", mh["want_ack"])
    print("via MQTT:", mh["via_mqtt"])
    print("channel hash:", f"0x{mh['channel_hash']:02X}")
    print("next hop:", mh["next_hop"])
    print("relay node:", mh["relay_node"])
    print("payload length:", packet_length)
    print("status: encrypted (not addressed to this decoder)")
    print("application: unknown (PortNum is encrypted)")
    

def print_nodeinfo(user):
    print("NodeInfo:")
    print("  Node ID:", user.get("id", "unknown"))
    print("  Long name:", user.get("long_name", "unknown"))
    print("  Short name:", user.get("short_name", "unknown"))

    mac = user.get("macaddr")
    if mac is not None:
        print(
            "  MAC:",
            ":".join(f"{b:02X}" for b in mac)
        )

    if "hw_model" in user:
        print("  Hardware model:", user["hw_model"])

    role = user.get("role", 0)
    print("  Role:", NODE_ROLES.get(role, f"UNKNOWN ({role})"))

    if "is_licensed" in user:
        print("  Licensed:", user["is_licensed"])

    if "is_unmessagable" in user:
        print("  Unmessagable:", user["is_unmessagable"])

    if "public_key" in user:
        print("  Public key:", user["public_key"])
        


def print_decoded_data(plaintext):
    data = format_data(plaintext)
    d = data["data"]

    portnum = d.get("portnum")
    application = PORTNUM_NAMES.get(portnum, "UNKNOWN")

    if portnum == 1:
        application = "TEXT_MESSAGE_APP"
    elif portnum == 67:
        application = "TELEMETRY_APP"

    text = data.get("text") or ""
    details = {}

    if data["status"] == "ok":
        print("PortNum: 1 (TEXT_MESSAGE_APP)")
        print("Text:", text)

    elif data["status"] == "nodeinfo":
        print("PortNum: 4 (NODEINFO_APP)")
        user = data["user"]
        print_nodeinfo(user)

        # Преобразуем bytes в строки, чтобы данные можно было
        # безопасно сериализовать в JSON.
        details = {
            key: (
                value.hex()
                if isinstance(value, bytes)
                else value
            )
            for key, value in user.items()
        }

        macaddr = user.get("macaddr")
        if macaddr is not None:
            details["macaddr"] = ":".join(
                f"{byte:02X}" for byte in macaddr
            )
            
    elif data["status"] == "detection_sensor":
        application = "DETECTION_SENSOR_APP"
        detection = data["detection_sensor"]
        details = detection
        print("PortNum: 36 (DETECTION_SENSOR_APP)")
        print("Detection sensor / location:", detection["name"])

    elif data["status"] == "telemetry":
        print("PortNum: 67 (TELEMETRY_APP)")
        details = {
            key: value
            for key, value in data["telemetry"].items()
            if value is not None
        }

        print("Telemetry:")
        for name, value in details.items():
            print(f"  {name}: {value}")
            
        print("\n=== TELEMETRY DEBUG ===")
        print("Telemetry payload:")
        debug_protobuf_fields(d["payload"])
        print("=== END TELEMETRY DEBUG ===\n")

    elif portnum == POSITION_APP:
        application = "POSITION_APP"
        position = decode_position(d["payload"])

        if position is not None:
            details = position
            print("Position:", position["latitude"], position["longitude"])
            print("Altitude:", position["altitude"])
        else:
            details = {
                "error": "Invalid position payload",
                "payload_hex": d["payload"].hex(),
            }
            print("Invalid position payload")

    elif data["status"] == "unsupported":
        application = PORTNUM_NAMES.get(portnum, "UNKNOWN")
        details = {
            "payload_hex": d["payload"].hex(),
        }
        print(f"PortNum: {portnum} ({application})")
        print("Payload:", d["payload"].hex())

    else:
        details = {
            "error": "Invalid Data protobuf",
            "payload_hex": d.get("payload", b"").hex(),
        }
        print("Invalid Data protobuf")

    return {
        "application": application,
        "portnum": portnum,
        "text": text,
        "details": details,
    }


def process_broadcast(mh, data_bytes, channels):
    if mh["to"] != BROADCAST_TO:
        return None

    hop_start = mh.get("hop_start")
    hop_limit = mh.get("hop_limit")
    channel = channels.get(mh["channel_hash"])

    base = {
        "result": "broadcast",
        "type": "BROADCAST",
        "from": mh["from"],
        "to": mh["to"],
        "packet_id": mh["packet_id"],
        "channel": mh["channel_hash"],
        "channel_name": channel.name if channel is not None else None,
        "hop_start": hop_start,
        "hop_limit": hop_limit,
        "hops": (
            hop_start - hop_limit
            if hop_start is not None and hop_limit is not None
            else None
        ),
        "text": "",
        "application": "unknown",
    }

    if channel is None:
        print("\n=== UNKNOWN CHANNEL ===")
        print("to:", f"{mh['to']:08X}")
        print("from:", f"{mh['from']:08X}")
        print("packet id:", f"{mh['packet_id']:08X}")
        print("channel hash:", f"{mh['channel_hash']:02X}")
        print("encrypted payload length:", len(data_bytes[16:-2]))
        print("Decryption skipped: unknown channel")

        return {
            **base,
            "channel_name": f"Unknown (0x{mh['channel_hash']:02X})",
            "result": "broadcast_unknown_channel",
            "application": "encrypted",
            "text": "[Unknown channel]",
        }

    try:
        decrypted = decrypt_packet(
            data_bytes,
            channels=channels,
            pki_key=None,
        )
    except Exception as exc:
        print(f"Broadcast decryption failed: {exc}")
        return {
            **base,
            "result": "broadcast_decryption_failed",
            "application": "encrypted",
            "text": "[Decryption failed]",
        }

    if decrypted["mode"] != "channel":
        print("Не удалось расшифровать broadcast-пакет")
        return {
            **base,
            "result": "broadcast_decryption_failed",
            "application": "encrypted",
            "text": "[Decryption failed]",
        }

    print("\n=== BROADCAST MESHTASTIC PACKET ===")
    print("to:", f"{mh['to']:08X}")
    print("from:", f"{mh['from']:08X}")
    print("packet id:", f"{mh['packet_id']:08X}")
    print("channel:", f"{mh['channel_hash']:02X}")
    print("channel name:", decrypted["channel"].name)
    print("channel PSK: configured")
    print("Data protobuf:", decrypted["plaintext"].hex())

    decoded_data = print_decoded_data(decrypted["plaintext"])

    return {
        **base,
        "channel_name": decrypted["channel"].name,
        **decoded_data,
    }


def process_pki(
    samples,
    mh,
    packet_length,
    payload_start,
    priv,
    own_node_id,
    peers,
    ref,
    ss,
    n,
    coding_rate=5,
):
    FROM = mh["from"]
    TO = mh["to"]

    white = generate_whitening_bytes(packet_length)
    src = source_map(coding_rate)

    init_nibs = initial_nibbles_from_to(TO, white)

    num_blocks = int(np.ceil(
        ((packet_length + 2) * 2 - len(init_nibs)) / 11
    ))

    spec, raw_count = build_spectra(
        samples,
        ref,
        num_blocks,
        payload_start,
        ss,
        samples_per_chirp=n,
        coding_rate=coding_rate,
    )

    blocks = []

    for i in range(num_blocks):
        b = candidate_block(
            i,
            spec,
            samples,
            ref,
            raw_count,
            src,
            payload_start,
            ss,
            samples_per_chirp=n,
            coding_rate=coding_rate,
        )
        blocks.append(b)

    target_plain = (
        TO.to_bytes(4, "little")
        + FROM.to_bytes(4, "little")
    )
    target_w = bytes(
        a ^ int(b)
        for a, b in zip(target_plain, white[:8])
    )

    target_n = []
    for b in target_w:
        target_n.extend([b & 0xF, b >> 4])

    b0 = []

    for tup in blocks[0]:
        sc, *sy = tup
        ns = decode_block(
            [gray_raw_symbol(x, n) for x in sy],
            coding_rate=coding_rate,
        )

        if (init_nibs + ns)[:15] == target_n[:15]:
            b0.append((sc, tuple(ns), tuple(sy)))

    print("block0 ID matches:", len(b0))

    if not b0:
        raise RuntimeError(
            "No block0 candidate matches the configured node IDs"
        )

    dec1 = []

    for tup in blocks[1]:
        sc, *sy = tup
        ns = decode_block(
            [gray_raw_symbol(x, n) for x in sy],
            coding_rate=coding_rate,
        )

        if ns[0] == target_n[15]:
            dec1.append((sc, tuple(ns), tuple(sy)))

    beam = [
        (bs + cs, bn + cn, by + cy)
        for bs, bn, by in b0[:20]
        for cs, cn, cy in dec1
    ]

    beam.sort(key=lambda x: x[0], reverse=True)
    beam = beam[:KEEP]

    print("beam after block1:", len(beam))

    for bi in range(2, num_blocks):
        dec = []

        for tup in blocks[bi]:
            sc, *sy = tup
            ns = decode_block(
                [gray_raw_symbol(x, n) for x in sy],
                coding_rate=coding_rate,
            )
            dec.append((sc, tuple(ns), tuple(sy)))

        bs = np.array([x[0] for x in beam])
        cs = np.array([x[0] for x in dec])
        mat = bs[:, None] + cs[None, :]
        flat = mat.ravel()

        k = min(KEEP, flat.size)
        inds = np.argpartition(flat, -k)[-k:]
        inds = inds[np.argsort(flat[inds])[::-1]]

        ncols = len(dec)
        new = []

        for ind in inds:
            i = int(ind // ncols)
            j = int(ind % ncols)

            bscore, bn, by = beam[i]
            cscore, cn, cy = dec[j]

            new.append((
                float(flat[ind]),
                bn + cn,
                by + cy,
            ))

        beam = new

    hits = []

    for item in beam:
        raw = make_plain(
            item[1],
            init_nibs,
            white,
            packet_length,
        )

        packet = parse_mesh_packet(raw)

        if packet is None:
            continue

        key = derive_packet_pki_key(
            priv,
            packet,
            own_node_id,
            peers,
        )

        if key is None:
            continue

        try:
            crypto = decrypt_pki(raw, key)
        except Exception:
            continue

        packet["ct"] = crypto["ct"]
        packet["tag"] = crypto["tag"]
        packet["extra"] = crypto["extra"]
        packet["pt"] = crypto["pt"]

        hits.append((item[0], packet, item[2]))

    print("AES HITS:", len(hits))

    if not hits:
        raise RuntimeError(
            "No authenticated Meshtastic packet found"
        )

    hits.sort(key=lambda x: x[0], reverse=True)
    score, h, _ = hits[0]

    packet_type = classify_packet(h)

    print("\n=== VERIFIED MESHTASTIC PACKET ===")
    print("score:", score)
    print("type:", packet_type.upper())
    print("to:", f"{h['to']:08X}")
    print("from:", f"{h['from']:08X}")
    print("packet id:", f"{h['packet_id']:08X}")
    print("flags:", f"{h['flags']:02X}")
    print("channel:", f"{h['channel']:02X}")
    print("next hop:", f"{h['next_hop']:02X}")
    print("relay node:", f"{h['relay_node']:02X}")
    print("extra nonce:", h["extra"].hex())
    print("Data protobuf:", h["pt"].hex())

    decoded_data = print_decoded_data(h["pt"])

    return {
        "result": "pki",
        "type": packet_type.upper(),
        "from": h["from"],
        "to": h["to"],
        "packet_id": h["packet_id"],
        "channel": h["channel"],
        "channel_name": None,
        "hop_start": mh.get("hop_start"),
        "hop_limit": mh.get("hop_limit"),
        "hops": (
            mh["hop_start"] - mh["hop_limit"]
            if mh.get("hop_start") is not None
            and mh.get("hop_limit") is not None
            else None
        ),
        **decoded_data,
    }
    
    
def correct_node_id(value, own_node_id):
    """
    Replace a node ID with own_node_id if exactly one
    hexadecimal digit differs.
    """
    if own_node_id is None:
        return value

    diff = value ^ own_node_id

    different_digits = sum(
        1
        for i in range(8)
        if ((diff >> (i * 4)) & 0xF) != 0
    )

    if different_digits == 1:
        print(
            f"Correcting node ID: "
            f"!{value:08X} -> !{own_node_id:08X}"
        )
        return own_node_id

    return value



def decode_packet(
    samples,
    mh,
    packet_length,
    data_bytes,
    payload_start,
    ref,
    ss,
    n,
    coding_rate=5,
):
    
    global channels
    channels = load_channels_from_settings()
    # Broadcast: расшифрованный пакет или информация об ошибке
    broadcast_result = process_broadcast(
        mh,
        data_bytes,
        channels,
    )

    if broadcast_result is not None:
        return broadcast_result
    
    
    priv, own_node_id, peers = load_pki_settings()
    
    mh = dict(mh)
    
    mh["from"] = correct_node_id(
        mh["from"],
        own_node_id
    )

    mh["to"] = correct_node_id(
        mh["to"],
        own_node_id
    )

    # Чужой private packet: содержимое недоступно для этого декодера
    if (
        mh["to"] != BROADCAST_TO
        and mh["to"] != own_node_id
        and mh["from"] != own_node_id
    ):
        print_encrypted_packet(mh, packet_length)

        return {
            "result": "foreign",
            "type": "PRIVATE",
            "from": mh["from"],
            "to": mh["to"],
            "packet_id": mh["packet_id"],
            "channel": mh["channel_hash"],
            "text": "[Encrypted]",
            "application": "encrypted",
            "channel_name": None,
            "hop_start": mh.get("hop_start"),
            "hop_limit": mh.get("hop_limit"),
            "hops": (
                mh["hop_start"] - mh["hop_limit"]
                if mh.get("hop_start") is not None
                and mh.get("hop_limit") is not None
                else None
            ),
        }

    # PKI: результат успешной расшифровки
    return process_pki(
        samples=samples,
        mh=mh,
        packet_length=packet_length,
        payload_start=payload_start,
        priv=priv,
        own_node_id=own_node_id,
        peers=peers,
        ref=ref,
        ss=ss,
        n=n,
        coding_rate=coding_rate,
    )

