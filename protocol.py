import struct
import base64

from config import BROADCAST_TO, NODE_ROLES

TEXT_MESSAGE_APP = 1
POSITION_APP = 3
NODEINFO_APP = 4
DETECTION_SENSOR_APP = 36
TELEMETRY_APP = 67

PORTNUM_NAMES = {
    1: "TEXT_MESSAGE_APP",
    3: "POSITION_APP",
    4: "NODEINFO_APP",
    36: "DETECTION_SENSOR_APP",
    65: "STORE_FORWARD_APP",
    67: "TELEMETRY_APP",
}

PACKET_FLAGS_HOP_LIMIT_MASK = 0x07
PACKET_FLAGS_WANT_ACK_MASK = 0x08
PACKET_FLAGS_VIA_MQTT_MASK = 0x10
PACKET_FLAGS_HOP_START_MASK = 0xE0
PACKET_FLAGS_HOP_START_SHIFT = 5


def parse_packet_flags(flags):
    """Decode the flags byte from the 16-byte Meshtastic radio header."""

    flags = int(flags)

    hop_limit = flags & PACKET_FLAGS_HOP_LIMIT_MASK
    want_ack = bool(flags & PACKET_FLAGS_WANT_ACK_MASK)
    via_mqtt = bool(flags & PACKET_FLAGS_VIA_MQTT_MASK)
    hop_start = (
        (flags & PACKET_FLAGS_HOP_START_MASK)
        >> PACKET_FLAGS_HOP_START_SHIFT
    )

    return {
        "raw": flags,
        "hop_limit": hop_limit,
        "want_ack": want_ack,
        "via_mqtt": via_mqtt,
        "hop_start": hop_start,
    }
    

def classify_packet(packet):
    """
    Classify a decoded Meshtastic MeshPacket.

    Returns:
        "broadcast" - packet addressed to all nodes
        "direct"    - packet addressed to a specific node
        "other"     - packet is unavailable or unknown
    """
    if packet is None:
        return "other"

    if packet["to"] == BROADCAST_TO:
        return "broadcast"

    return "direct"


def normalize_broadcast_id(node_id):
    """
    Correct a probable one-nibble decoding error in broadcast ID.

    If 7 of 8 hexadecimal digits are F, treat the ID as FFFFFFFF.
    """
    text = f"{node_id:08X}"

    if text.count("F") >= 6:
        print('TO BROADCAST', text)
        return BROADCAST_TO

    return node_id


def parse_radio_header(raw):
    """Разобрать 16-байтовый заголовок Meshtastic."""

    raw = bytes(raw)

    if len(raw) < 16:
        raise ValueError("Mesh header must contain at least 16 bytes")

    flags = raw[12]
    flag_info = parse_packet_flags(flags)
    
    to = int.from_bytes(raw[0:4], "little")
    to = normalize_broadcast_id(to)

    return {
        "to": to,
        "from": int.from_bytes(raw[4:8], "little"),
        "packet_id": int.from_bytes(raw[8:12], "little"),
        "flags": flags,
        "channel_hash": raw[13],
        "hop_limit": flag_info["hop_limit"],
        "want_ack": flag_info["want_ack"],
        "via_mqtt": flag_info["via_mqtt"],
        "hop_start": flag_info["hop_start"],
        "next_hop": raw[14],
        "relay_node": raw[15],
    }


def parse_mesh_packet(raw):
    """
    Разобрать заголовок Meshtastic и полезную нагрузку.
    """

    raw = bytes(raw)

    if len(raw) < 18:
        return None

    header = parse_radio_header(raw)

    return {
        "raw": raw,
        **header,
        "channel": header["channel_hash"],
        "payload": raw[16:-2],
        "phy_crc": raw[-2:],
        "is_broadcast": header["to"] == BROADCAST_TO,
    }
    
def _read_varint(data, pos):
    """Прочитать protobuf varint. Возвращает (value, new_pos)."""
    value = 0

    for shift in range(0, 70, 7):
        if pos >= len(data):
            raise ValueError("Incomplete varint")

        byte = data[pos]
        pos += 1

        if shift == 63 and byte > 1:
            raise ValueError("Varint overflow")

        value |= (byte & 0x7F) << shift

        if byte < 0x80:
            return value, pos

    raise ValueError("Varint is too long")


def _parse_data_proto(pt):
    """
    Проверить и разобрать Data protobuf за один проход.

    Возвращает словарь с portnum, payload и raw.
    При ошибке возвращает None.
    """
    pt = bytes(pt)

    if not pt:
        return None

    i = 0
    portnum = None
    payload = b""
    found_portnum = False
    found_payload = False

    try:
        while i < len(pt):
            key, i = _read_varint(pt, i)
            field = key >> 3
            wire = key & 7

            if field == 0:
                return None

            if field == 1:
                if wire != 0:
                    return None

                portnum, i = _read_varint(pt, i)

                if portnum > 255:
                    return None

                found_portnum = True

            elif field == 2:
                if wire != 2:
                    return None

                length, i = _read_varint(pt, i)

                if length > len(pt) - i:
                    return None

                payload = pt[i:i + length]
                i += length
                found_payload = True

            elif wire == 0:
                _, i = _read_varint(pt, i)

            elif wire == 1:
                if i + 8 > len(pt):
                    return None
                i += 8

            elif wire == 2:
                length, i = _read_varint(pt, i)

                if length > len(pt) - i:
                    return None

                i += length

            elif wire == 5:
                if i + 4 > len(pt):
                    return None
                i += 4

            else:
                # Неподдерживаемый wire type, включая группы.
                return None

    except ValueError:
        return None

    if not found_portnum or not found_payload:
        return None

    return {
        "portnum": portnum,
        "payload": payload,
        "raw": pt,
    }
    
def validate_data_proto(pt):
    """Проверить Data protobuf."""
    return _parse_data_proto(pt) is not None


def decode_data_proto(pt):
    """Разобрать Data protobuf."""
    pt = bytes(pt)
    result = _parse_data_proto(pt)

    if result is None:
        return {
            "portnum": None,
            "payload": b"",
            "raw": pt,
        }

    return result


def _read_position_fields(payload):
    """Разобрать поля protobuf Position."""
    payload = bytes(payload)
    fields = {}
    i = 0

    try:
        while i < len(payload):
            key, i = _read_varint(payload, i)
            field = key >> 3
            wire = key & 7

            if field == 0:
                return None

            if wire == 0:
                value, i = _read_varint(payload, i)

            elif wire == 1:
                if i + 8 > len(payload):
                    return None
                value = int.from_bytes(payload[i:i + 8], "little")
                i += 8

            elif wire == 2:
                length, i = _read_varint(payload, i)
                if length > len(payload) - i:
                    return None
                value = payload[i:i + length]
                i += length

            elif wire == 5:
                if i + 4 > len(payload):
                    return None
                value = int.from_bytes(payload[i:i + 4], "little")
                i += 4

            else:
                return None

            fields[field] = (wire, value)

    except ValueError:
        return None

    return fields


def decode_position(payload):
    """Извлечь координаты и высоту из protobuf Position."""
    fields = _read_position_fields(payload)

    if fields is None:
        return None

    def signed_fixed32(field):
        item = fields.get(field)

        if item is None or item[0] != 5:
            return None

        value = item[1]
        return value - (1 << 32) if value & 0x80000000 else value

    lat = signed_fixed32(1)
    lon = signed_fixed32(2)

    if lat is None or lon is None:
        return None

    altitude = None
    item = fields.get(3)

    if item is not None and item[0] == 0:
        altitude = item[1]
        if altitude & 0x80000000:
            altitude -= 1 << 32

    result = {
        "latitude": lat / 10_000_000,
        "longitude": lon / 10_000_000,
        "altitude": altitude,
    }

    item = fields.get(4)
    if item is not None and item[0] == 5:
        result["time"] = item[1]

    return result


def _parse_telemetry_proto(payload):
    """Разобрать Telemetry и вложенный DeviceMetrics."""

    payload = bytes(payload)
    i = 0
    result = {
        "time_unix": None,
        "battery_percent": None,
        "voltage": None,
        "channel_utilization": None,
        "air_util_tx": None,
        "uptime_seconds": None,
    }

    try:
        while i < len(payload):
            key, i = _read_varint(payload, i)
            field = key >> 3
            wire = key & 7

            if field == 0:
                return None

            if wire == 0:
                value, i = _read_varint(payload, i)

                if field == 1:
                    result["time_unix"] = value

            elif wire == 1:
                if i + 8 > len(payload):
                    return None
                i += 8

            elif wire == 2:
                length, i = _read_varint(payload, i)

                if length > len(payload) - i:
                    return None

                value = payload[i:i + length]
                i += length

                if field == 2:
                    metrics = _parse_device_metrics(value)

                    if metrics is None:
                        return None

                    result.update(metrics)

                elif field == 3:
                    metrics = _parse_environment_metrics(value)

                    if metrics is None:
                        return None

                    result.update(metrics)

            elif wire == 5:
                if i + 4 > len(payload):
                    return None

                # Пропускаем FIXED32: поле time_unix
                # в Telemetry должно быть VARINT.
                i += 4

            else:
                return None

    except ValueError:
        return None

    return result


def _parse_environment_metrics(payload):
    """Разобрать EnvironmentMetrics из Meshtastic protobuf."""

    payload = bytes(payload)
    i = 0

    result = {}

    # EnvironmentMetrics: поля типа float (FIXED32).
    float_fields = {
        1: "temperature",
        2: "relative_humidity",
        3: "barometric_pressure",
        5: "voltage",
        6: "current",
        8: "distance",
        9: "lux",
        10: "white_lux",
        11: "ir_lux",
        12: "uv_lux",
        13: "wind_speed",
        14: "wind_direction",
        15: "wind_gust",
        16: "wind_lull",
        17: "weight",
        18: "radiation",
        19: "rainfall_1h",
        20: "rainfall_24h",
        22: "soil_temperature",
    }

    try:
        while i < len(payload):
            key, i = _read_varint(payload, i)
            field = key >> 3
            wire = key & 7

            if field == 0:
                return None

            if wire == 0:
                value, i = _read_varint(payload, i)

                if field == 4:
                    result["gas_resistance"] = value
                elif field == 7:
                    result["iaq"] = value
                elif field == 21:
                    result["soil_moisture"] = value

            elif wire == 1:
                if i + 8 > len(payload):
                    return None
                i += 8

            elif wire == 2:
                length, i = _read_varint(payload, i)

                if length > len(payload) - i:
                    return None

                i += length

            elif wire == 5:
                if i + 4 > len(payload):
                    return None

                raw = payload[i:i + 4]
                i += 4

                name = float_fields.get(field)

                if name is not None:
                    result[name] = struct.unpack("<f", raw)[0]

            else:
                return None

    except (ValueError, struct.error):
        return None

    return result


def _parse_user_proto(payload):
    """Разобрать protobuf User из NODEINFO_APP."""
    payload = bytes(payload)
    fields = {}
    i = 0

    names = {
        1: "id",
        2: "long_name",
        3: "short_name",
        4: "macaddr",
        5: "hw_model",
        6: "is_licensed",
        7: "role",
        8: "public_key",
        9: "is_unmessagable",
    }

    try:
        while i < len(payload):
            key, i = _read_varint(payload, i)
            field, wire = key >> 3, key & 7

            if field == 0:
                return None

            if wire == 0:
                value, i = _read_varint(payload, i)

            elif wire == 1:
                if i + 8 > len(payload):
                    return None
                value = payload[i:i + 8]
                i += 8

            elif wire == 2:
                length, i = _read_varint(payload, i)
                if length > len(payload) - i:
                    return None
                value = payload[i:i + length]
                i += length

            elif wire == 5:
                if i + 4 > len(payload):
                    return None
                value = payload[i:i + 4]
                i += 4

            else:
                return None

            name = names.get(field)
            if name is not None:
                fields[name] = (wire, value)

    except ValueError:
        return None

    result = {}

    for name in ("id", "long_name", "short_name"):
        item = fields.get(name)
        if item is not None:
            if item[0] != 2:
                return None
            try:
                result[name] = item[1].decode("utf-8")
            except UnicodeDecodeError:
                return None

    item = fields.get("macaddr")
    if item is not None:
        if item[0] != 2:
            return None
        result["macaddr"] = item[1]

    for name in (
        "hw_model",
        "role",
        "is_licensed",
        "is_unmessagable",
    ):
        item = fields.get(name)
        
        if item is not None:
            if item[0] != 0:
                return None

            if name == "role":
                result[name] = NODE_ROLES.get(
                    item[1],
                    f"UNKNOWN ({item[1]})",
                )
            elif name in ("is_licensed", "is_unmessagable"):
                result[name] = bool(item[1])
            else:
                result[name] = item[1]
            
    result.setdefault("role", NODE_ROLES.get(0))
    
    item = fields.get("public_key")
    if item is not None:
        if item[0] != 2:
            return None

        result["public_key"] = base64.b64encode(
            item[1]
        ).decode("ascii")

    return result


def _parse_device_metrics(payload):
    """Разобрать DeviceMetrics."""

    payload = bytes(payload)
    i = 0
    result = {
        "battery_percent": None,
        "voltage": None,
        "channel_utilization": None,
        "air_util_tx": None,
        "uptime_seconds": None,
    }

    fields = {
        1: "battery_percent",
        2: "voltage",
        3: "channel_utilization",
        4: "air_util_tx",
        5: "uptime_seconds",
    }

    try:
        while i < len(payload):
            key, i = _read_varint(payload, i)
            field = key >> 3
            wire = key & 7

            if field == 0:
                return None

            name = fields.get(field)

            if wire == 0:
                value, i = _read_varint(payload, i)

                if name in ("battery_percent", "uptime_seconds"):
                    result[name] = value

            elif wire == 5:
                if i + 4 > len(payload):
                    return None

                raw = payload[i:i + 4]
                i += 4

                if name in (
                    "voltage",
                    "channel_utilization",
                    "air_util_tx",
                ):
                    result[name] = struct.unpack("<f", raw)[0]

            elif wire == 1:
                if i + 8 > len(payload):
                    return None
                i += 8

            elif wire == 2:
                length, i = _read_varint(payload, i)
                if length > len(payload) - i:
                    return None
                i += length

            else:
                return None

    except ValueError:
        return None

    return result


def format_data(pt):
    d = decode_data_proto(pt)

    if d["portnum"] is None:
        return {
            "status": "invalid",
            "data": d,
            "text": None,
            "telemetry": None,
        }

    if d["portnum"] == TEXT_MESSAGE_APP:
        try:
            text = d["payload"].decode("utf-8")
        except UnicodeDecodeError:
            return {
                "status": "invalid",
                "data": d,
                "text": None,
                "telemetry": None,
            }

        return {
            "status": "ok",
            "data": d,
            "text": text,
            "telemetry": None,
        }
        
    if d["portnum"] == NODEINFO_APP:
        user = _parse_user_proto(d["payload"])

        if user is None:
            return {
                "status": "invalid",
                "data": d,
                "text": None,
                "telemetry": None,
                "user": None,
            }

        return {
            "status": "nodeinfo",
            "data": d,
            "text": None,
            "telemetry": None,
            "user": user,
        }
        
    if d["portnum"] == DETECTION_SENSOR_APP:
        detection = _parse_detection_sensor(d["payload"])

        if detection is None:
            return {
                "status": "invalid",
                "data": d,
                "text": None,
                "telemetry": None,
                "detection_sensor": None,
            }

        return {
            "status": "detection_sensor",
            "data": d,
            "text": None,
            "telemetry": None,
            "detection_sensor": detection,
        }
        
    if d["portnum"] == TELEMETRY_APP:
        telemetry = _parse_telemetry_proto(d["payload"])

        if telemetry is None:
            return {
                "status": "invalid",
                "data": d,
                "text": None,
                "telemetry": None,
            }

        return {
            "status": "telemetry",
            "data": d,
            "text": None,
            "telemetry": telemetry,
        }

    return {
        "status": "unsupported",
        "data": d,
        "text": None,
        "telemetry": None,
    }
    
### удалить    
def debug_protobuf_fields(payload, indent="  "):
    """Показать все protobuf-поля без предположений об их назначении."""
    payload = bytes(payload)
    i = 0

    print(f"{indent}Raw hex: {payload.hex()}")
    print(f"{indent}Length: {len(payload)} bytes")
    print(f"{indent}Fields:")

    try:
        while i < len(payload):
            start = i
            key, i = _read_varint(payload, i)
            field = key >> 3
            wire = key & 7

            if field == 0:
                print(f"{indent}  ERROR: field number 0 at offset {start}")
                return

            if wire == 0:
                value, i = _read_varint(payload, i)
                print(
                    f"{indent}  field={field}, wire=VARINT, "
                    f"value={value} (0x{value:x})"
                )

            elif wire == 1:
                if i + 8 > len(payload):
                    raise ValueError("Incomplete fixed64")

                raw = payload[i:i + 8]
                i += 8
                value = struct.unpack("<d", raw)[0]

                print(
                    f"{indent}  field={field}, wire=FIXED64, "
                    f"hex={raw.hex()}, double={value}"
                )

            elif wire == 2:
                
                if len(raw) > 0:
                    print(f"{indent}    Nested protobuf:")
                    debug_protobuf_fields(raw, indent + "      ")
                
                length, i = _read_varint(payload, i)

                if length > len(payload) - i:
                    raise ValueError("Incomplete length-delimited field")

                raw = payload[i:i + length]
                i += length

                print(
                    f"{indent}  field={field}, wire=LEN, "
                    f"length={length}, hex={raw.hex()}"
                )

                try:
                    text = raw.decode("utf-8")
                    if text and all(ch.isprintable() for ch in text):
                        print(f"{indent}    UTF-8: {text!r}")
                except UnicodeDecodeError:
                    pass

            elif wire == 5:
                if i + 4 > len(payload):
                    raise ValueError("Incomplete fixed32")

                raw = payload[i:i + 4]
                i += 4
                value = struct.unpack("<f", raw)[0]

                print(
                    f"{indent}  field={field}, wire=FIXED32, "
                    f"hex={raw.hex()}, float={value}"
                )

            else:
                print(
                    f"{indent}  ERROR: unsupported wire type={wire} "
                    f"at offset={start}"
                )
                return

    except (ValueError, struct.error) as exc:
        print(f"{indent}  PARSE ERROR at offset={i}: {exc}")
        
def _parse_detection_sensor(payload):
    """Разобрать DetectionSensor payload.

    В наблюдаемом пакете payload имеет вид 0A <length> <UTF-8 name>,
    то есть строка находится в protobuf field 1. Неизвестные поля
    пропускаются, чтобы парсер оставался совместимым с расширениями.
    """
    payload = bytes(payload)
    fields = _read_position_fields(payload)

    if fields is None:
        return None

    item = fields.get(1)
    if item is None or item[0] != 2 or not isinstance(item[1], bytes):
        return None

    try:
        name = item[1].decode("utf-8")
    except UnicodeDecodeError:
        return None

    if not name or not all(ch.isprintable() for ch in name):
        return None

    return {"name": name}
