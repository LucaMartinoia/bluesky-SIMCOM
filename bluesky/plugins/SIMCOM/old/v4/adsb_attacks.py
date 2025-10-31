import numpy as np
from math import radians
from bluesky import (
    core,
    stack,
    traf,
    ref,
    sim,
    settings,
)  # , settings, navdb, sim, scr, tools
import pyModeS as pms
from bluesky.tools.aero import ft, Rearth, kts, nm
from random import randint
from bluesky.tools.misc import txt2alt
from bluesky.plugins.SIMCOM.v4.adsb_encoder import (
    ADSB_identification,
    ADSB_position,
    ADSB_velocity,
    id2idx,
)

"""SIMCOM module that implements cyber-attacks on the ADS-B protocol."""

"""
class ADSBattacks(core.Entity):
    """Class that implements man-in-the-middle cyber attacks on ADS-B data."""

    def __init__(self):
        """Initializing the attack class."""

        super().__init__()

        # List of implemented functions
        self.attack_str = "FREEZE, HIDE, JUMP, MGHOST, GHOST, NONE, STATUS, OFF"

        # Create arrays for the attack arguments and attack type
        with self.settrafarrays():
            self.arg = []
            self.type = np.array([], dtype="<U20")
            self.test = np.array([])

    def create(self, n=1):
        """When new aircraft are created, they are appended with a new field that stores
        the cyber-attack parameters."""

        super().create(n)

        self.arg[-n:] = ({} for _ in range(n))
        self.type[-n:] = ["NONE"] * n
        self.test[-n:] = 10

    # --------------------------------------------------------------------
    #                      ATTACKS
    # --------------------------------------------------------------------

    def man_in_the_middle(self, adsb):
        """This function is called every 0.5s in protocol.py.
        It overwrites the ADS-B messages depending on the attack before they are sent to the GPU.
        """

        self.mitm_freeze(adsb)
        self.mitm_hide(adsb)
        self.mitm_jump(adsb)
        self.mitm_ghost(adsb)

    def mitm_freeze(self, adsb):
        """Simulate jamming by freezing ADS-B outputs to last known values."""

        mask = self.type == "FREEZE"
        indices = np.where(mask)[0]

        for i in indices:
            # Is AC initialized?
            if self.arg[i]["init"]:
                # Overwrite the ADSB messages with frozen ones
                adsb.msg_pos_e[i] = self.arg[i]["msg_pos_o"]
                adsb.msg_pos_o[i] = self.arg[i]["msg_pos_e"]
            else:
                # Save last known ADSB messages
                self.arg[i]["msg_pos_e"] = adsb.msg_pos_e[i]
                self.arg[i]["msg_pos_o"] = adsb.msg_pos_o[i]
                # Set initialization flag to True
                self.arg[i]["init"] = True

    def mitm_hide(self, adsb):
        """Simulate jamming by deleting ADS-B outputs."""

        mask = self.type == "HIDE"
        indices = np.where(mask)[0]

        if len(indices) > 0:
            adsb.msg_pos_e[mask] = None
            adsb.msg_pos_o[mask] = None

    def mitm_jump(self, adsb):
        """Simulate a jummping attack that changes reported ADS-B position."""

        mask = self.type == "JUMP"
        indices = np.where(mask)[0]

        for i in indices:
            lat, lon = pms.adsb.airborne_position(
                str(adsb.msg_pos_e[i]),
                str(adsb.msg_pos_o[i]),
                0,
                1,
            )
            alt = pms.adsb.altitude(str(adsb.msg_pos_e[i])) * ft

            adsb.lat[i] = lat + self.arg[i]["lat"]
            adsb.lon[i] = lon + self.arg[i]["lon"]
            adsb.altbaro[i] = alt + self.arg[i]["alt"]

            adsb.msg_pos_o[i] = ADSB_position(adsb, i, False)
            adsb.msg_pos_e[i] = ADSB_position(adsb, i, True)

    def mitm_ghost(self, adsb):
        """Simulate ghost aircraft."""

        mask = self.type == "GHOST"
        indices = np.where(mask)[0]

        # We loop backward, so when we call del self.arg the indices
        # that get shifted are already processed
        for i in sorted(indices, reverse=True):
            # Is AC initialized?
            if self.arg[i]["init"] == 1:
                # Computes ADSB messages
                adsb.msg_pos_o[i] = ADSB_position(adsb, i, False)
                adsb.msg_pos_e[i] = ADSB_position(adsb, i, True)
                adsb.msg_id[i] = ADSB_identification(adsb, i)
                adsb.msg_v[i] = ADSB_velocity(adsb, i)
            else:
                # Delete selected AC
                self.del_ghost(adsb, i)
                # Delete the attack args
                del self.arg[i]
                self.type = np.delete(self.type, i)

    def init_ghosts(self, adsb):
        """Inizializes all the GHOST aircraft."""

        # Check non-initialized GHOST and call their creation
        mask = self.type == "GHOST"
        indices = np.where(mask)[0]

        for i in indices:
            if self.arg[i]["init"] == 0:
                # Initialize GHOST AC
                self.cre_ghost(adsb, i)
                # Set initialization flag to 1
                self.arg[i]["init"] = 1

    def cre_ghost(self, adsb, i):
        """Inizializes GHOST aircraft."""

        # Append GHOST values to the ADS-B data
        if self.arg[i]["id"] not in traf.id:
            traf.id.append(self.arg[i]["id"])
        else:
            idtmp = chr(randint(65, 90)) + chr(randint(65, 90)) + "{:>05}"
            traf.id.append(idtmp.format(0))
        adsb.icao = np.append(adsb.icao, self.arg[i]["icao"])
        adsb.callsign.append(self.arg[i]["callsign"])

        adsb.altbaro = np.append(adsb.altbaro, self.arg[i]["alt"])
        adsb.altGNSS = np.append(adsb.altGNSS, self.arg[i]["alt"])
        adsb.lat = np.append(adsb.lat, self.arg[i]["lat"])
        adsb.lon = np.append(adsb.lon, self.arg[i]["lon"])
        rads = np.deg2rad(self.arg[i]["trk"])
        gsnorth = self.arg[i]["gs"] * np.cos(rads)
        gseast = self.arg[i]["gs"] * np.sin(rads)
        adsb.gsnorth = np.append(adsb.gsnorth, gsnorth)
        adsb.gseast = np.append(adsb.gseast, gseast)
        adsb.vs = np.append(adsb.vs, 0)
        adsb.gs = np.append(adsb.gs, self.arg[i]["gs"])
        adsb.trk = np.append(adsb.trk, self.arg[i]["trk"])

        adsb.capability = np.append(adsb.capability, 5)
        adsb.ss = np.append(adsb.ss, 0)

        # Conflict detection variables
        adsb.cd.rpz = np.append(adsb.cd.rpz, settings.asas_pzr * nm)
        adsb.cd.hpz = np.append(adsb.cd.hpz, settings.asas_pzh * ft)
        adsb.cd.dtlookahead = np.append(adsb.cd.dtlookahead, settings.asas_dtlookahead)

        # Compute initial ADSB messages
        adsb.msg_pos_o = np.append(adsb.msg_pos_o, ADSB_position(adsb, i, False))
        adsb.msg_pos_e = np.append(adsb.msg_pos_e, ADSB_position(adsb, i, True))
        adsb.msg_id = np.append(adsb.msg_id, ADSB_identification(adsb, i))
        adsb.msg_v = np.append(adsb.msg_v, ADSB_velocity(adsb, i))

    def del_ghost(self, adsb, i):
        """Deletes GHOST aircraft."""

        # Delete GHOST values from the ADS-B data
        traf.id.pop(i)
        adsb.callsign.pop(i)
        adsb.icao = np.delete(adsb.icao, i)

        adsb.altbaro = np.delete(adsb.altbaro, i)
        adsb.altGNSS = np.delete(adsb.altGNSS, i)
        adsb.lat = np.delete(adsb.lat, i)
        adsb.lon = np.delete(adsb.lon, i)
        adsb.gsnorth = np.delete(adsb.gsnorth, i)
        adsb.gseast = np.delete(adsb.gseast, i)
        adsb.vs = np.delete(adsb.vs, i)
        adsb.gs = np.delete(adsb.gs, i)
        adsb.trk = np.delete(adsb.trk, i)

        adsb.capability = np.delete(adsb.capability, i)
        adsb.ss = np.delete(adsb.ss, i)

        adsb.cd.rpz = np.delete(adsb.cd.rpz, i)
        adsb.cd.hpz = np.delete(adsb.cd.hpz, i)
        adsb.cd.dtlookahead = np.delete(adsb.cd.dtlookahead, i)

        adsb.msg_pos_o = np.delete(adsb.msg_pos_o, i)
        adsb.msg_pos_e = np.delete(adsb.msg_pos_e, i)
        adsb.msg_id = np.delete(adsb.msg_id, i)
        adsb.msg_v = np.delete(adsb.msg_v, i)

    def update_ghost_pos(self, adsb):
        """Update the ADS-B position for GHOST aircraft."""

        mask = self.type == "GHOST"
        if not np.any(mask):
            return  # Nothing to update

        adsb.altbaro[mask] = np.round(adsb.altbaro[mask] + adsb.vs[mask] * sim.simdt, 6)
        adsb.altGNSS[mask] = adsb.altbaro[mask]
        adsb.lat[mask] = adsb.lat[mask] + np.degrees(
            sim.simdt * adsb.gsnorth[mask] / Rearth
        )
        coslat = np.cos(np.deg2rad(adsb.lat[mask]))
        adsb.lon[mask] = adsb.lon[mask] + np.degrees(
            sim.simdt * adsb.gseast[mask] / (coslat * Rearth)
        )

    # --------------------------------------------------------------------
    #                      STACK FUNCTIONS
    # --------------------------------------------------------------------

    @stack.commandgroup(name="ATTACK", brief="ATTACK commands")
    def attack(self):
        """Cyber-attack related commands."""

        return True, (f"ATTACK command\nPossible subcommands: {self.attack_str}.")

    @attack.subcommand(name="FREEZE", brief="FREEZE acid")
    def attack_freeze(self, acid: "acid"):  # type: ignore
        """FREEZE attack for a given aircraft."""

        self.type[acid] = "FREEZE"
        self.arg[acid]["init"] = False  # Initialization flag

        return True, f"{traf.id[acid]} is under FREEZE attack."

    @attack.subcommand(name="HIDE", brief="HIDE acid")
    def attack_hide(self, acid: "acid"):  # type: ignore
        """HIDE attack for a given aircraft."""

        self.type[acid] = "HIDE"
        self.arg[acid] = {
            "msg_pos_o": None,
            "msg_pos_e": None,
        }
        return True, f"{traf.id[acid]} is under HIDE attack."

    @attack.subcommand(name="JUMP", brief="JUMP acid,lat-diff,lon-diff,alt-diff")
    def attack_jump(self, acid: "acid", lat: float, lon: float, alt: str):  # type: ignore
        """JUMP attack for a given aircraft."""

        self.type[acid] = "JUMP"
        self.arg[acid] = {
            "lat": lat,
            "lon": lon,
            "alt": txt2alt(alt),
        }
        return True, f"{traf.id[acid]} is under JUMP attack."

    @attack.subcommand(name="GHOST", brief="GHOST acid,lat,lon,hdg,alt,spd")
    def attack_ghost(
        self, callsign: str, lat: float, lon: float, hdg: float, alt: str, gs: float
    ):
        """Creates a GHOST aircraft."""

        self.type = np.append(self.type, "GHOST")
        self.arg.append({})

        self.arg[-1]["alt"] = txt2alt(alt)
        self.arg[-1]["gs"] = gs * kts
        self.arg[-1]["id"] = callsign
        self.arg[-1]["icao"] = f"{randint(0, 0xFFFFFF):06X}"
        self.arg[-1]["callsign"] = callsign
        self.arg[-1]["lat"] = lat
        self.arg[-1]["lon"] = lon
        self.arg[-1]["trk"] = hdg

        self.arg[-1]["init"] = 0  # Initialization flag

        return True, f"GHOST aircraft created."

    @attack.subcommand(name="MGHOST", brief="MGHOST num")
    def attack_mghost(self, num: int):
        """Creates multiple random GHOST aircraft."""

        area = ref.area.bbox

        for _ in range(num):
            # Generate random data
            id = chr(randint(65, 90)) + chr(randint(65, 90)) + "{:>05}"
            callsign = id.format(0)
            lat = np.random.rand() * (area[2] - area[0]) + area[0]
            lon = np.random.rand() * (area[3] - area[1]) + area[1]
            hdg = np.random.randint(1, 360)
            alt = str(np.random.randint(2000, 39000))
            gs = np.random.randint(250, 450)

            self.attack_ghost(callsign, lat, lon, hdg, alt, gs)

        return True, f"{num} GHOST aircraft created."

    @stack.command(name="DELGHOST", brief="DELGHOST [acid]")
    def remove_ghost(self, acid: str = ""):  # type: ignore
        """Remove selected aircraft. If no ACID is provided, remove ALL ghost aircraft instead."""

        if acid != "":
            i = id2idx(acid)
            if i == -1:
                return False, "Aircraft does not exists."
            elif self.type[i] != "GHOST":
                return False, f"{acid} is not a GHOST aircraft"
            else:
                self.arg[i]["init"] = 2
                return True, f"GHOST aircraft {acid} deleted."
        else:
            mask = self.type == "GHOST"
            indices = np.where(mask)[0]

            for i in indices:
                self.arg[i]["init"] = 2
            return True

    @attack.subcommand(name="NONE", brief="NONE acid")
    def attack_none(self, acid: "acid"):  # type: ignore
        """Clear any attack for a given aircraft."""

        if self.type[acid] == "GHOST":
            return (
                False,
                f"Cannot clear GHOST aircraft. Use DELGHOST instead.",
            )

        self.type[acid] = "NONE"
        self.arg[acid] = {}

        return True, f"{traf.id[acid]} is not under attack."

    @attack.subcommand(name="STATUS", brief="STATUS acid")
    def attack_status(self, acid: "acid"):  # type: ignore
        """Show current attack status for a given aircraft."""

        return (
            True,
            f"Aircraft {traf.id[acid]} is currently under {self.type[acid]} attack.",
        )

    @attack.subcommand(name="OFF")
    def attack_off(self):  # type: ignore
        """Show current attack status for a given aircraft."""

        self.remove_ghost()

        for attack, id in zip(self.type, traf.id):
            if attack != "GHOST":
                self.attack_none(id)

        return (
            True,
            f"All aircraft returned to NONE status.",
        )
