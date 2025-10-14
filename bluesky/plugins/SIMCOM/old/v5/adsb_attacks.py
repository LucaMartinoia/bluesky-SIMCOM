import numpy as np
from bluesky import core, stack, sim, traf  # , settings, navdb, sim, scr, tools
import pyModeS as pms
from bluesky.tools.aero import ft, Rearth
from bluesky.tools.misc import txt2alt
from bluesky.plugins.SIMCOM.v4.adsb_protocol import (
    ADSB_identification,
    ADSB_position,
    ADSB_velocity,
)

type_codes = dict(identification=4, position=9, velocity=19)
attack_list_str = "FREEZE, JUMP, HIDE, NONE, STATUS"

"""
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
        "update": adsbattacks.update_ADSBpos,
        # Reset contest
        "reset": adsbattacks.reset,
    }
    return config


class ADSBattacks(core.Entity):
    @core.timed_function(dt=0.5, hook="update")  # runs every 0.5 simulated seconds
    def man_in_the_middle(self):
        # Compute ADS-B messages for all aircraft
        mask = traf.ADSBattack != "NONE"
        idxs = np.where(mask)[0]
        for i in idxs:
            if traf.ADSBattack[i] != "FREEZE":
                traf.ADSBmsg_pos_o[i] = ADSB_position(traf.id[i], False)
                traf.ADSBmsg_pos_e[i] = ADSB_position(traf.id[i], True)
                traf.ADSBmsg_v[i] = ADSB_velocity(traf.id[i])
            traf.ADSBmsg_id[i] = ADSB_identification(traf.id[i])

        self.mitm_freeze()
        self.mitm_hide()
        self.mitm_jump()

    def update_ADSBpos(self):
        # Update position
        mask = traf.ADSBattack != "NONE"
        if not np.any(mask):
            return  # Nothing to update
        traf.ADSBaltBaro[mask] = np.round(
            traf.ADSBaltBaro[mask] + traf.ADSBvs[mask] * sim.simdt, 6
        )
        traf.ADSBlat[mask] = traf.ADSBlat[mask] + np.degrees(
            sim.simdt * traf.ADSBgsnorth[mask] / Rearth
        )
        coslat = np.cos(np.deg2rad(traf.ADSBlat[mask]))
        traf.ADSBlon[mask] = traf.ADSBlon[mask] + np.degrees(
            sim.simdt * traf.ADSBgseast[mask] / (coslat * Rearth)
        )

    # --------------------------------------------------------------------
    #                      ATTACKS
    # --------------------------------------------------------------------

    def mitm_freeze(self):
        """Simulate jamming by freezing ADS-B outputs to last known values."""
        mask = traf.ADSBattack == "FREEZE"
        return

    def mitm_hide(self):
        """Simulate jamming by deleting ADS-B outputs."""
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

    @stack.commandgroup(name="ATTACK", brief=f"ATTACK commands: {attack_list_str}")
    def attack(self):
        """Group of attack-related commands."""
        return True, (f"ATTACK command\nPossible subcommands: {attack_list_str}")

    @attack.subcommand(name="FREEZE", brief="FREEZE acid")
    def attack_freeze(self, acid: "acid"):  # type: ignore
        """Enable freezing attack for a given aircraft."""
        traf.ADSBattack[acid] = "FREEZE"
        return True, f"{traf.id[acid]} is under FREEZE attack."

    @attack.subcommand(name="HIDE", brief="HIDE acid")
    def attack_hide(self, acid: "acid"):  # type: ignore
        """Enable jamming attack for a given aircraft."""
        traf.ADSBattack[acid] = "HIDE"
        traf.ADSBmsg_pos_o[acid] = None
        traf.ADSBmsg_pos_e[acid] = None
        return True, f"{traf.id[acid]} is under HIDE attack."

    @attack.subcommand(name="JUMP", brief="JUMP acid, lat-diff,lon-diff,alt-diff")
    def attack_jump(self, acid: "acid", lat: float, lon: float, alt: str):  # type: ignore
        """Enable jumping attack for a given aircraft."""
        traf.ADSBattack[acid] = "JUMP"
        traf.ADSBlat = lat
        traf.ADSBlon = lon
        traf.ADSBaltBaro = txt2alt(alt)
        return True, f"{traf.id[acid]} is under JUMP attack."

    @attack.subcommand(name="NONE", brief="NONE acid")
    def attack_none(self, acid: "acid"):  # type: ignore
        """Clear any attack for a given aircraft."""
        traf.ADSBattack[acid] = "NONE"
        return True, f"{traf.id[acid]} is under NONE attack."

    @attack.subcommand(name="STATUS", brief="STATUS acid")
    def attack_status(self, acid: "acid"):  # type: ignore
        """Show current attack status for a given aircraft."""
        return (
            True,
            f"Aircraft {traf.id[acid]} is currently under {traf.ADSBattack[acid]} attack.",
        )
