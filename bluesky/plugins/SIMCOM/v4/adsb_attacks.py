import numpy as np
from bluesky import core, stack, traf  # , settings, navdb, sim, scr, tools
import pyModeS as pms
from bluesky.tools.aero import ft

attack_types = dict()


def init_plugin():
    """Plugin initialisation function."""

    # Instantiate singleton entity
    adsbprotocol = ADSBattacks()

    print("\n--- Loading ADS-B plugin: attacks ---\n")

    # Configuration parameters
    config = {
        "plugin_name": "ADSBATTACKS",
        "plugin_type": "sim",
        # The update function is called after traffic is updated.
        # "update": adsbprotocol.update,
        # Reset contest
        # "reset": adsbprotocol.reset,
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
    def man_in_middle(self):
        self.mitm_freeze()

    # --------------------------------------------------------------------
    #                      ATTACKS
    # --------------------------------------------------------------------

    def mitm_freeze(self):
        """Simulate jamming by freezing ADS-B outputs to last known values."""

        # If AC under JAMMING attack, do nothing.
        mask = traf.ADSBattack == "FREEZE"

    def mitm_hide(self):
        """Simulate jamming by deleting ADS-B outputs."""

        # If AC under JAMMING attack, do nothing.
        mask = traf.ADSBattack == "HIDE"

        traf.ADSBmsg_pos_e[mask] = None
        traf.ADSBmsg_pos_o[mask] = None

    # --------------------------------------------------------------------
    #                      STACK COMMANDS
    # --------------------------------------------------------------------

    @stack.commandgroup(name="ATTACK", brief="ATTACK commands: FREEZE, NONE, STATUS")
    def attack(self):
        """Group of attack-related commands."""
        return True, ("ATTACK command\nPossible subcommands: FREEZE, NONE, STATUS")

    @attack.subcommand(name="FREEZE", brief="FREEZE acid")
    def attack_freeze(self, acid: "acid"):  # type: ignore
        """Enable jamming attack for a given aircraft."""
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

        return True, f"{traf.id[acid]} is under HIDE attack."

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
