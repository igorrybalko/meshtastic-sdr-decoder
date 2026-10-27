from dotenv import load_dotenv

load_dotenv()

SAMPLE_RATE_HZ: int = 1_024_000

# Длина преамбулы в символах (стандартное значение LoRa/Meshtastic).
PREAMBLE_SYMBOLS: int = 8

HEADER_OFFSET = 2_098

BROADCAST_TO = 0xFFFFFFFF

NODE_ROLES = {
    0: "CLIENT",
    1: "CLIENT_MUTE",
    2: "ROUTER",
    3: "ROUTER_CLIENT",
    4: "REPEATER",
    5: "TRACKER",
    6: "SENSOR",
    7: "TAK",
    8: "CLIENT_HIDDEN",
    9: "LOST_AND_FOUND",
    10: "TAK_TRACKER",
    11: "ROUTER_LATE",
    12: "CLIENT_BASE",
}
