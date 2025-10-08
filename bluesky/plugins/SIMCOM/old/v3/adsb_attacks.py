import numpy as np
from bluesky import stack, traf  # , settings, navdb, sim, scr, tools
import pyModeS as pms
from bluesky.tools.aero import ft

attack_types = {}


def jamming(adsb_position_even, adsb_position_odd, adsb_identification, mask):
    """Simulate jamming by freezing ADS-B outputs to last known values."""

    # For each attacked aircraft, decode the even/odd position,
    # and update the traf.ADSB* arrays accordingly.
    for idx, (msg_even, msg_odd) in enumerate(
        zip(adsb_position_even, adsb_position_odd)
    ):
        # Find the actual index in traf arrays
        traf_idx = np.where(mask)[0][idx]

        # Decode position (lat, lon, alt) using pyModeS
        try:
            lat, lon = pms.adsb.airborne_position(msg_even, msg_odd, 0, 1)
            alt = pms.adsb.altitude(msg_even)
        except Exception:
            lat, lon, alt = np.nan, np.nan, np.nan

        # Update traf.ADSB* arrays
        traf.ADSBlat[traf_idx] = lat
        traf.ADSBlon[traf_idx] = lon
        traf.ADSBaltBaro[traf_idx] = round(alt * ft)


attack_types.update({"JAMMING": jamming})


# --------------------------------------------------------------------
#                      STACK COMMANDS
# --------------------------------------------------------------------


@stack.commandgroup(name="ATTACK", brief="ATTACK commands: JAMMING, NONE, STATUS")
def attack():
    """Group of attack-related commands."""


@attack.subcommand(name="JAMMING", brief="JAMMING acid")
def attack_jamming(acid: "acid"):  # type: ignore
    """Enable jamming attack for a given aircraft."""
    traf.ADSBattack[acid] = "JAMMING"
    return True, f"{traf.id[acid]} is under JAMMING attack."


@attack.subcommand(name="NONE", brief="NONE acid")
def attack_none(acid: "acid"):  # type: ignore
    """Clear any attack for a given aircraft."""
    traf.ADSBattack[acid] = "NONE"
    return True, f"{traf.id[acid]} is under NONE attack."


@attack.subcommand(name="STATUS", brief="STATUS acid")
def attack_status(acid: "acid"):  # type: ignore
    """Show current attack status for a given aircraft."""
    return (
        True,
        f"Aircraft {traf.id[acid]} is currently under {traf.ADSBattack[acid]} attack.",
    )
