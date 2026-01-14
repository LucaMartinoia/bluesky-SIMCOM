import numpy as np
from bluesky import (
    core,
    stack,
    traf,
    ref,
    sim,
)  # , settings, navdb, sim, scr, tools
import pyModeS as pms
from bluesky.tools.aero import ft, Rearth, kts
from random import randint
from bluesky.tools.misc import txt2alt
from bluesky.plugins.SIMCOM.tools import id2idx
import bluesky.plugins.SIMCOM.adsb_encoder as encoder

"""
SIMCOM module that implements cyber-attacks on the ADS-B protocol.

TODO:
- Refactor ghosts
- Implement other attack types (surveillance, icao)
"""


class ADSBattacks(core.Entity):
    """
    Class that implements man-in-the-middle cyber attacks on ADS-B data.
    """

    def __init__(self) -> None:
        """
        Initializing the attack class.
        """

        super().__init__()

        # List of implemented attacks
        self.attack_str = (
            "FREEZE, HIDE, JUMP, MGHOST, GHOST, NONE, STATUS, RESET, ON, OFF"
        )
        self.flag = True  # Module ON/OFF flag

        # Create arrays for the attack arguments and attack type
        with self.settrafarrays():
            self.arg = []  # Attack arguments
            self.type = np.array([], dtype="<U20")  # Attack type

            # Attacker cached data
            self.alt = np.array([], dtype=float)  # [m]
            self.lat = np.array([], dtype=float)  # latitude [deg]
            self.lon = np.array([], dtype=float)  # longitude [deg]
            self.gsnorth = np.array([], dtype=float)  # ground speed [m/s]
            self.gseast = np.array([], dtype=float)
            self.vs = np.array([], dtype=float)  # vertical speed [m/s]
            self.trk = np.array([], dtype=float)  # track angle [deg]
            self.icao = []
            self.callsign = []

    def create(self, n: int = 1) -> None:
        """
        When new aircraft are created, they are appended with a new field that stores
        the cyber-attack parameters.
        """

        super().create(n)

        # Empty arguments and NONE attack for newly created aircraft
        self.arg[-n:] = [{} for _ in range(n)]
        self.type[-n:] = ["NONE"] * n

        # Attacker cached data
        self.alt[-n:] = np.nan
        self.lat[-n:] = np.nan
        self.lon[-n:] = np.nan
        self.gsnorth[-n:] = np.nan
        self.gseast[-n:] = np.nan
        self.vs[-n:] = np.nan
        self.trk[-n:] = np.nan
        self.icao[-n:] = [""] * n
        self.callsign[-n:] = [""] * n

    # --------------------------------------------------------------------
    #                      ATTACKS
    # --------------------------------------------------------------------

    def apply_attacks(self, msgs, index: int) -> None:
        """
        This function is called every 0.5s in protocol.py.
        It overwrites the ADS-B messages depending on the attack before they are sent to the GPU.

        The index is not necessary, it is only to avoid having to do a lookup of pms.icao(msg) to find the index.
        """

        type = self.type[index]

        if type == "FREEZE":
            self.apply_freeze(msgs, index)
        elif type == "HIDE":
            self.apply_hide(msgs, index)
        elif type == "JUMP":
            self.apply_jump(msgs, index)
        elif type == "GHOST":
            self.apply_ghost(msgs, index)

    def apply_freeze(self, msgs, index: int) -> None:
        """
        Simulate replay attacks by freezing ADS-B outputs to last known values.
        """

        # Is AC initialized?
        if self.arg[index]["init"]:
            # Overwrite the ADS-B messages with frozen ones
            msgs[index].position_even[0] = self.arg[index]["msg_pos_e"]
            msgs[index].position_odd[0] = self.arg[index]["msg_pos_o"]
        else:
            # Save last known ADS-B messages
            self.arg[index]["msg_pos_e"] = msgs[index].position_even[0]
            self.arg[index]["msg_pos_o"] = msgs[index].position_odd[0]
            # Set initialization flag to True
            self.arg[index]["init"] = True

    def apply_hide(self, msgs, index: int) -> None:
        """
        Simulate jamming by deleting ADS-B outputs.
        """

        # Delete current messages
        msgs[index].position_even[0] = ""
        msgs[index].position_odd[0] = ""
        msgs[index].identification[0] = ""
        msgs[index].velocity[0] = ""

    def apply_jump(self, msgs, index: int):
        """
        Simulate a jummping attack that changes reported ADS-B position.
        """

        # Read ADS-B data from messages
        try:
            lat, lon = pms.adsb.airborne_position(  # type: ignore
                msgs[index].position_odd[0],
                msgs[index].position_even[0],
                0,
                1,
            )
            self.icao[index] = pms.icao(msgs[index].position_odd[0])
            alt = pms.adsb.altitude(msgs[index].position_even[0]) * ft  # type:ignore

            # Validate altitude
            if alt < 0 or alt > 50175 * ft:  # choose sensible max for your sim
                alt = np.nan
        except Exception:
            lat, lon, alt = np.nan, np.nan, np.nan

        # Update cache with new position
        self.lat[index] = lat + self.arg[index]["lat"]
        self.lon[index] = lon + self.arg[index]["lon"]
        self.alt[index] = alt + self.arg[index]["alt"]

        capability = 5
        TC = 9
        status = 0
        antenna = 1
        t0 = 0

        # Encode new messages
        msgs[index].position_odd[0] = encoder._airborne_position(
            capability,
            self.icao[index],
            TC,
            status,
            antenna,
            self.alt[index],
            t0,
            False,
            self.lat[index],
            self.lon[index],
        )
        msgs[index].position_even[0] = encoder._airborne_position(
            capability,
            self.icao[index],
            TC,
            status,
            antenna,
            self.alt[index],
            t0,
            True,
            self.lat[index],
            self.lon[index],
        )

    def apply_ghost(self, msgs, index: int) -> None:
        """
        Simulate ghost aircraft.
        """

        # Is AC initialized?
        if self.arg[index]["init"] == 1:

            # Computes ADS-B messages
            msgs[index].position_odd[0] = encoder._airborne_position(
                ca=5,
                icao=self.icao[index],
                TC=9,
                status=0,
                antenna=1,
                alt=self.alt[index],
                time=0,
                even=False,
                lat=self.lat[index],
                lon=self.lon[index],
            )
            msgs[index].position_even[0] = encoder._airborne_position(
                ca=5,
                icao=self.icao[index],
                TC=9,
                status=0,
                antenna=1,
                alt=self.alt[index],
                time=0,
                even=True,
                lat=self.lat[index],
                lon=self.lon[index],
            )
            msgs[index].identification[0] = encoder._identification(
                ca=5, icao=self.icao[index], TC=4, ec=3, callsign=self.callsign[index]
            )
            msgs[index].velocity[0] = encoder._airborne_velocity(
                ca=5,
                icao=self.icao[index],
                IC_flag=0,
                NACv=3,
                gs_north=self.gsnorth[index],
                gs_east=self.gseast[index],
                vert_src=1,
                s_vert=self.vs[index],
                GNSS_alt=self.alt[index],
                baro_alt=self.alt[index],
            )
        else:
            # Initialize GHOST AC
            self.cre_ghost(msgs, index)
            # Set initialization flag to 1
            self.arg[index]["init"] = 1

    def cre_ghost(self, msgs, i: int) -> None:
        """
        Inizializes GHOST aircraft.
        """

        # Assign GHOST values to the ADS-B data
        self.callsign[i] = self.arg[i]["callsign"]
        # If callsign in ID list, also copy the ICAO address
        if self.callsign[i] in traf.id:
            matching_index = traf.id.index(self.callsign[i])
            self.icao[i] = pms.icao(msgs[matching_index].identification[0])
        else:
            # Otherwise random ICAO
            self.icao[i] = f"{randint(0, 0xFFFFFF):06X}"

        rads = np.deg2rad(self.arg[i]["trk"])
        gsnorth = self.arg[i]["gs"] * np.cos(rads)
        gseast = self.arg[i]["gs"] * np.sin(rads)

        # Flight data
        self.alt[i] = self.arg[i]["alt"]
        self.lat[i] = self.arg[i]["lat"]
        self.lon[i] = self.arg[i]["lon"]
        self.gsnorth[i] = gsnorth
        self.gseast[i] = gseast
        self.trk[i] = self.arg[i]["trk"]
        self.vs[i] = 0

    def update_ghosts(self) -> None:
        """
        Update the ADS-B position for GHOST aircraft.
        """

        # Find ghosts
        mask = self.type == "GHOST"
        if not np.any(mask):
            return  # Nothing to update

        # Move in time simdt
        self.alt[mask] = np.round(self.alt[mask] + self.vs[mask] * sim.simdt, 6)
        self.lat[mask] = self.lat[mask] + np.degrees(
            sim.simdt * self.gsnorth[mask] / Rearth
        )
        coslat = np.cos(np.deg2rad(self.lat[mask]))
        self.lon[mask] = self.lon[mask] + np.degrees(
            sim.simdt * self.gseast[mask] / (coslat * Rearth)
        )

    # --------------------------------------------------------------------
    #                      STACK FUNCTIONS
    # --------------------------------------------------------------------

    @stack.commandgroup(name="ATTACK", brief="ATTACK commands")
    def attack(self):
        """
        Cyber-attack related commands.
        """

        return True, (f"ATTACK command\nPossible subcommands: {self.attack_str}.")

    @attack.subcommand(name="FREEZE", brief="FREEZE acid")
    def attack_freeze(self, acid: "acid"):  # type: ignore
        """
        FREEZE attack for a given aircraft.
        """

        self.type[acid] = "FREEZE"
        self.arg[acid]["init"] = False  # Initialization flag

        return True, f"{traf.id[acid]} is under FREEZE attack."

    @attack.subcommand(name="HIDE", brief="HIDE acid")
    def attack_hide(self, acid: "acid"):  # type: ignore
        """
        HIDE attack for a given aircraft.
        """

        self.type[acid] = "HIDE"
        return True, f"{traf.id[acid]} is under HIDE attack."

    @attack.subcommand(name="JUMP", brief="JUMP acid,lat-diff,lon-diff,alt-diff")
    def attack_jump(self, acid: "acid", lat: float, lon: float, alt: str):  # type: ignore
        """
        JUMP attack for a given aircraft.
        """

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
        """
        Creates a GHOST aircraft.
        """

        # If callsign already exists, create a new id
        if callsign in traf.id:
            id = chr(randint(65, 90)) + chr(randint(65, 90)) + "{:>05}"
            id = id.format(0)
        else:
            id = callsign

        # Create new aircraft
        traf.cre(  # type:ignore
            id,
            actype="",
            aclat=0.0,
            aclon=0.0,
            achdg=0.0,
            acalt=0.0,
            acspd=0.0,
        )

        # Set all ghost true attributes to None
        for attrname in dir(traf):
            child = getattr(traf, attrname)
            # Skip builtins and non-object members
            if (
                attrname.startswith("_")
                or callable(child)
                or (attrname in ["groups", "perf"])
            ) and attrname != "_ArrVars":
                continue
            if hasattr(child, "_ArrVars"):
                # Loop over all numpy arrays
                for varname in child._ArrVars:
                    var = getattr(child, varname)
                    var[-1] = np.nan

        # Set attack type
        self.type[-1] = "GHOST"
        # Save attack attributes
        self.arg[-1]["alt"] = txt2alt(alt)
        self.arg[-1]["gs"] = gs * kts
        self.arg[-1]["id"] = callsign
        self.arg[-1]["callsign"] = callsign
        self.arg[-1]["lat"] = lat
        self.arg[-1]["lon"] = lon
        self.arg[-1]["trk"] = hdg

        self.arg[-1]["init"] = 0  # Initialization flag

        return True, f"GHOST aircraft created."

    @attack.subcommand(name="MGHOST", brief="MGHOST num")
    def attack_mghost(self, num: int):
        """
        Creates multiple random GHOST aircraft.
        """

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

            # Create ghost aircraft
            self.attack_ghost(callsign, lat, lon, hdg, alt, gs)

        return True, f"{num} GHOST aircraft created."

    @stack.command(name="DELGHOST", brief="DELGHOST [acid]")
    def remove_ghost(self, acid: str = ""):  # type: ignore
        """
        Remove selected aircraft. If no ACID is provided, remove ALL ghost aircraft instead.
        """

        if acid != "":
            i = id2idx(acid)
            if i == -1:
                return False, "Aircraft does not exists."
            elif self.type[i] != "GHOST":
                return False, f"{acid} is not a GHOST aircraft"
            else:
                traf.delete(i)
        else:
            mask = self.type == "GHOST"
            indices = np.where(mask)[0]

            for i in sorted(indices, reverse=True):
                traf.delete(i)
            return True

    @attack.subcommand(name="NONE", brief="NONE acid")
    def attack_none(self, acid: "acid"):  # type: ignore
        """
        Clear any attack for a given aircraft.
        """

        if self.type[acid] == "GHOST":
            self.remove_ghost(acid)
            return (
                False,
                f"Cannot clear GHOST aircraft. Use DELGHOST instead.",
            )

        self.type[acid] = "NONE"
        self.arg[acid] = {}

        return True, f"{traf.id[acid]} is not under attack."

    @attack.subcommand(name="STATUS", brief="STATUS acid")
    def attack_status(self, acid: "acid"):  # type: ignore
        """
        Show current attack status for a given aircraft.
        """

        return (
            True,
            f"Aircraft {traf.id[acid]} is currently under {self.type[acid]} attack.",
        )

    # TODO: Attack on Surveillance status

    @attack.subcommand(name="RESET")
    def attack_reset(self):
        """
        Remove all attacks.
        """

        # Remove ghost aircraft
        self.remove_ghost()

        # Set other aircraft to NONE
        for idx, attack in enumerate(self.type):
            if attack != "GHOST":
                self.attack_none(idx)

        return (
            True,
            f"All aircraft returned to NONE status.",
        )

    @attack.subcommand(name="ON")
    def attack_on(self):
        """
        Enable module.
        """

        self.flag = True

        return (
            True,
            f"Attack module enabled.",
        )

    @attack.subcommand(name="OFF")
    def attack_off(self):
        """
        Disable module.
        """

        self.flag = False

        return (
            True,
            f"Attack module disabled.",
        )
