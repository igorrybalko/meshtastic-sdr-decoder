"""
Meshtastic radio configuration.

This module contains Meshtastic modem presets and region parameters.
Frequency slots are calculated from region + preset instead of being
stored as individual frequencies.
"""


# ---------------------------------------------------------------------------
# Modem presets
# ---------------------------------------------------------------------------

MODEM_PRESETS = {
    "SHORT_TURBO": {
        "bandwidth": 500,
        "spreading_factor": 7,
        "coding_rate": 5,
    },

    "SHORT_FAST": {
        "bandwidth": 250,
        "spreading_factor": 7,
        "coding_rate": 5,
    },

    "SHORT_SLOW": {
        "bandwidth": 250,
        "spreading_factor": 8,
        "coding_rate": 5,
    },

    "MEDIUM_FAST": {
        "bandwidth": 250,
        "spreading_factor": 9,
        "coding_rate": 5,
    },

    "MEDIUM_SLOW": {
        "bandwidth": 250,
        "spreading_factor": 10,
        "coding_rate": 5,
    },

    "MEDIUM_TURBO": {
        "bandwidth": 500,
        "spreading_factor": 9,
        "coding_rate": 5,
    },

    "LONG_FAST": {
        "bandwidth": 250,
        "spreading_factor": 11,
        "coding_rate": 5,
    },

    "LONG_TURBO": {
        "bandwidth": 500,
        "spreading_factor": 11,
        "coding_rate": 8,
    },

    "LONG_MODERATE": {
        "bandwidth": 125,
        "spreading_factor": 11,
        "coding_rate": 8,
    },

    "LONG_SLOW": {
        "bandwidth": 125,
        "spreading_factor": 12,
        "coding_rate": 8,
    },

    "LITE_FAST": {
        "bandwidth": 125,
        "spreading_factor": 9,
        "coding_rate": 5,
    },

    "LITE_SLOW": {
        "bandwidth": 125,
        "spreading_factor": 10,
        "coding_rate": 5,
    },

    "NARROW_FAST": {
        "bandwidth": 62.5,
        "spreading_factor": 7,
        "coding_rate": 6,
    },

    "NARROW_SLOW": {
        "bandwidth": 62.5,
        "spreading_factor": 8,
        "coding_rate": 6,
    },

    "TINY_FAST": {
        "bandwidth": 15.6,
        "spreading_factor": 7,
        "coding_rate": 5,
    },

    "TINY_SLOW": {
        "bandwidth": 15.6,
        "spreading_factor": 8,
        "coding_rate": 6,
    },
}


# ---------------------------------------------------------------------------
# Region profiles
# ---------------------------------------------------------------------------

PROFILES = {
    "STD": {
        "spacing": 0.0,
        "padding": 0.0,
        "licensed_only": False,
    },

    "EU868": {
        "spacing": 0.0,
        "padding": 0.0,
        "licensed_only": False,
    },

    "LITE": {
        "spacing": 0.4,
        "padding": 0.0375,
        "licensed_only": False,
    },

    "NARROW": {
        "spacing": 0.0,
        "padding": 0.0104,
        "licensed_only": False,
    },
}


# ---------------------------------------------------------------------------
# Regions
# ---------------------------------------------------------------------------

REGIONS = {
    "EU_433": {
        "name": "EU 433 MHz",
        "freq_start": 433.0,
        "freq_end": 434.0,
        "duty_cycle": 10,
        "power_limit": 10,
        "wide_lora": False,
        "profile": "STD",
        "default_preset": "LONG_FAST",
        "override_slot": 0,
    },

    "UA_433": {
        "name": "Ukraine 433 MHz",
        "freq_start": 433.0,
        "freq_end": 434.7,
        "duty_cycle": 10,
        "power_limit": 10,
        "wide_lora": False,
        "profile": "STD",
        "default_preset": "LONG_FAST",
        "override_slot": 0,
    },

    "EU_868": {
        "name": "EU 868 MHz",
        "freq_start": 869.4,
        "freq_end": 869.65,
        "duty_cycle": 10,
        "power_limit": 27,
        "wide_lora": False,
        "profile": "EU868",
        "default_preset": "LONG_FAST",
        "override_slot": 0,
    },

    "US": {
        "name": "US",
        "freq_start": 902.0,
        "freq_end": 928.0,
        "duty_cycle": 100,
        "power_limit": 30,
        "wide_lora": False,
        "profile": "STD",
        "default_preset": "LONG_FAST",
        "override_slot": 0,
    },
}


# Presets allowed for the first version of our application.
#
# We can expand this list later without changing the rest of the code.

REGION_PRESETS = {
    "EU_433": [
        "LONG_FAST",
        "LONG_SLOW",
        "MEDIUM_SLOW",
        "MEDIUM_FAST",
        "SHORT_SLOW",
        "SHORT_FAST",
        "LONG_MODERATE",
        "SHORT_TURBO",
        "LONG_TURBO",
        "MEDIUM_TURBO",
    ],

    "UA_433": [
        "LONG_FAST",
        "LONG_SLOW",
        "MEDIUM_SLOW",
        "MEDIUM_FAST",
        "SHORT_SLOW",
        "SHORT_FAST",
        "LONG_MODERATE",
        "SHORT_TURBO",
        "LONG_TURBO",
        "MEDIUM_TURBO",
    ],

    "EU_868": [
        "LONG_FAST",
        "LONG_SLOW",
        "MEDIUM_SLOW",
        "MEDIUM_FAST",
        "SHORT_SLOW",
        "SHORT_FAST",
        "LONG_MODERATE",
    ],

    "US": [
        "LONG_FAST",
        "LONG_SLOW",
        "MEDIUM_SLOW",
        "MEDIUM_FAST",
        "SHORT_SLOW",
        "SHORT_FAST",
        "LONG_MODERATE",
        "SHORT_TURBO",
        "LONG_TURBO",
        "MEDIUM_TURBO",
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_preset(preset_name):
    """Return modem preset configuration."""
    return MODEM_PRESETS[preset_name]


def get_region(region_name):
    """Return region configuration."""
    return REGIONS[region_name]


def get_bandwidth(region_name, preset_name):
    """Return LoRa bandwidth in kHz."""

    region = get_region(region_name)
    preset = get_preset(preset_name)

    if region["wide_lora"]:
        # LORA_24 is intentionally not supported yet.
        raise ValueError("Wide LoRa regions are not supported")

    return preset["bandwidth"]


def get_slot_width(region_name, preset_name):
    """Return frequency slot width in MHz."""

    region = get_region(region_name)
    bandwidth = get_bandwidth(region_name, preset_name)

    profile = PROFILES[region["profile"]]

    return (
        profile["spacing"]
        + profile["padding"] * 2
        + bandwidth / 1000
    )


def get_num_slots(region_name, preset_name):
    """Return number of available frequency slots."""

    region = get_region(region_name)
    slot_width = get_slot_width(region_name, preset_name)

    return round(
        (
            region["freq_end"]
            - region["freq_start"]
            + PROFILES[region["profile"]]["spacing"]
        )
        / slot_width
    )


def get_slot_frequency(region_name, preset_name, slot):
    """
    Calculate the center frequency of a frequency slot.

    Returns frequency in MHz.
    """

    region = get_region(region_name)
    bandwidth = get_bandwidth(region_name, preset_name)
    profile = PROFILES[region["profile"]]

    slot_width = get_slot_width(region_name, preset_name)

    frequency = (
        region["freq_start"]
        + bandwidth / 2000
        + profile["padding"]
        + slot * slot_width
    )

    return round(frequency, 6)


def _calculate_hash(text):
    """
    Meshtastic-compatible DJB2-style hash used for frequency slot selection.
    """

    hash_value = 5381

    for char in text:
        hash_value = ((hash_value << 5) + hash_value + ord(char)) & 0xFFFFFFFF

    return hash_value


def get_default_slot(region_name, preset_name):
    """
    Calculate the default frequency slot.

    For regions without an explicit override, the unnamed/default
    channel uses the modem preset display name for the hash.
    """

    region = get_region(region_name)
    num_slots = get_num_slots(region_name, preset_name)

    if num_slots == 0:
        return 0

    override_slot = region["override_slot"]

    if override_slot > 0:
        return override_slot - 1

    preset_display_names = {
        "SHORT_TURBO": "ShortTurbo",
        "SHORT_FAST": "ShortFast",
        "SHORT_SLOW": "ShortSlow",
        "MEDIUM_FAST": "MediumFast",
        "MEDIUM_SLOW": "MediumSlow",
        "MEDIUM_TURBO": "MediumTurbo",
        "LONG_FAST": "LongFast",
        "LONG_TURBO": "LongTurbo",
        "LONG_MODERATE": "LongMod",
        "LONG_SLOW": "LongSlow",
        "LITE_FAST": "LiteFast",
        "LITE_SLOW": "LiteSlow",
        "NARROW_FAST": "NarrowFast",
        "NARROW_SLOW": "NarrowSlow",
        "TINY_FAST": "TinyFast",
        "TINY_SLOW": "TinySlow",
    }

    display_name = preset_display_names[preset_name]

    return _calculate_hash(display_name) % num_slots