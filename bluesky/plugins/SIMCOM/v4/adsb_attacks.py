import numpy as np
from bluesky import core, stack, traf  # , settings, navdb, sim, scr, tools
import pyModeS as pms
from bluesky.tools.aero import ft
from bluesky.tools.misc import txt2alt
from bluesky.plugins.SIMCOM.v4.adsb_protocol import (
    ADSB_identification,
    ADSB_position,
    ADSB_velocity,
)

type_codes = dict(identification=4, position=9, velocity=19)


def init_plugin():
    """Plugin initialisation function."""

    # Instantiate singleton entity
    adsbattacks = ADSBattacks()

    print("\n--- Loading ADS-B plugin: attacks ---\n")

    # Configuration parameters
    config = {
        "plugin_name": "ADSBATTACKS",
        "plugin_type": "sim",
        # The update function is called after traffic is updated.
        # "update": adsbprotocol.update,
        # Reset contest
        "reset": adsbattacks.reset,
    }
    return config


class ADSBattacks(core.Entity):
    def __init__(self):
        super().__init__()

        # All classes deriving from Entity can register lists and numpy arrays
        # that hold per-aircraft data. This way, their size is automatically
        # updated when aircraft are created or deleted in the simulation.
        with traf.settrafarrays():
            traf.ADSBattack_arg = []

    def create(self, n=1):
        super().create(n)

        traf.ADSBattack_arg[-n:] = [{} for _ in range(n)]

    @core.timed_function(dt=0.5, hook="update")  # runs every 0.5 simulated seconds
    def man_in_the_middle(self):
        self.mitm_freeze()
        self.mitm_hide()
        self.mitm_jump()

    def reset(self):
        """Clear all traffic data upon simulation reset."""

        # Some child reset functions depend on a correct value of self.ntraf
        traf.ntraf = 0
        # This ensures that the traffic arrays (which size is dynamic)
        # are all reset as well, so all lat,lon,sdp etc but also objects adsb
        super().reset()

    # --------------------------------------------------------------------
    #                      ATTACKS
    # --------------------------------------------------------------------

    def mitm_freeze(self):
        """Simulate jamming by freezing ADS-B outputs to last known values."""

        # If AC under JAMMING attack, do nothing.
        mask = traf.ADSBattack == "FREEZE"
        indices = np.where(mask)[0]

        for i in indices:
            traf.ADSBmsg_pos_e[i] = traf.ADSBattack_arg[i]["msg_pos_o"]
            traf.ADSBmsg_pos_o[i] = traf.ADSBattack_arg[i]["msg_pos_e"]

    def mitm_hide(self):
        """Simulate jamming by deleting ADS-B outputs."""

        # If AC under JAMMING attack, do nothing.
        mask = traf.ADSBattack == "HIDE"

        if len(np.where(mask)[0]) > 0:
            traf.ADSBmsg_pos_e[mask] = None
            traf.ADSBmsg_pos_o[mask] = None

    def mitm_jump(self):
        """Simulate a jummping attack that changes ADS-B position"""

        mask = traf.ADSBattack == "JUMP"
        indices = np.where(mask)[0]

        for i in indices:
            lat, lon = pms.adsb.airborne_position(
                str(traf.ADSBmsg_pos_e[i]),
                str(traf.ADSBmsg_pos_o[i]),
                0,
                1,
            )
            alt = pms.adsb.altitude(str(traf.ADSBmsg_pos_e[i])) * ft

            traf.ADSBlat[i] = lat + traf.ADSBattack_arg[i]["lat"]
            traf.ADSBlon[i] = lon + traf.ADSBattack_arg[i]["lon"]
            traf.ADSBaltBaro[i] = alt + traf.ADSBattack_arg[i]["alt"]

            traf.ADSBmsg_pos_o[i] = ADSB_position(traf.id[i], False)
            traf.ADSBmsg_pos_e[i] = ADSB_position(traf.id[i], True)

    # --------------------------------------------------------------------
    #                      STACK COMMANDS
    # --------------------------------------------------------------------

    @stack.commandgroup(name="ATTACK", brief="ATTACK commands: FREEZE, NONE, STATUS")
    def attack(self):
        """Group of attack-related commands."""
        return True, ("ATTACK command\nPossible subcommands: FREEZE, NONE, STATUS")

    @attack.subcommand(name="FREEZE", brief="FREEZE acid")
    def attack_freeze(self, acid: "acid"):  # type: ignore
        """Enable freezing attack for a given aircraft."""
        traf.ADSBattack[acid] = "FREEZE"
        traf.ADSBattack_arg[acid] = {
            "msg_pos_o": traf.ADSBmsg_pos_o[acid],
            "msg_pos_e": traf.ADSBmsg_pos_e[acid],
        }

        return True, f"{traf.id[acid]} is under FREEZE attack."

    @attack.subcommand(name="HIDE", brief="HIDE acid")
    def attack_hide(self, acid: "acid"):  # type: ignore
        """Enable jamming attack for a given aircraft."""
        traf.ADSBattack[acid] = "HIDE"
        traf.ADSBattack_arg[acid] = {
            "msg_pos_o": None,
            "msg_pos_e": None,
        }

        return True, f"{traf.id[acid]} is under HIDE attack."

    @attack.subcommand(name="JUMP", brief="JUMP acid, lat-diff,lon-diff,alt-diff")
    def attack_jump(self, acid: "acid", lat: float, lon: float, alt: str):  # type: ignore
        """Enable jumping attack for a given aircraft."""
        traf.ADSBattack[acid] = "JUMP"
        traf.ADSBattack_arg[acid] = {
            "lat": lat,
            "lon": lon,
            "alt": txt2alt(alt),
        }
        return True, f"{traf.id[acid]} is under JUMP attack."

    @attack.subcommand(name="NONE", brief="NONE acid")
    def attack_none(self, acid: "acid"):  # type: ignore
        """Clear any attack for a given aircraft."""
        traf.ADSBattack[acid] = "NONE"
        traf.ADSBattack_arg[acid] = {}

        return True, f"{traf.id[acid]} is under NONE attack."

    @attack.subcommand(name="STATUS", brief="STATUS acid")
    def attack_status(self, acid: "acid"):  # type: ignore
        """Show current attack status for a given aircraft."""
        return (
            True,
            f"Aircraft {traf.id[acid]} is currently under {traf.ADSBattack[acid]} attack.",
        )
