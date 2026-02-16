import numpy as np
from types import SimpleNamespace
from bluesky import core, stack, traf, ref, sim, settings, tools
from bluesky.tools.aero import ft, Rearth, kts, nm
from bluesky.tools.misc import txt2alt
from bluesky.plugins.SIMCOM.tools import id2idx
from bluesky.plugins.SIMCOM.adsbout import ADSBout
from bluesky.plugins.SIMCOM.adsbin import ADSBin
from bluesky.plugins.SIMCOM.physical_layer import Transmission

"""
Module that implements cyber-attacks on the ADS-B protocol.
"""


class Attacker(core.Entity):
    """
    Class that implements Cyber-attackers for ADS-B traffic.
    """

    def __init__(self, loc) -> None:
        super().__init__()

        # List of implemented commands
        self.command_str = (
            "FREEZE, HIDE, JUMP, MGHOST, GHOST, CONFGHOST, NONE, STATUS, RESET, TOGGLE"
        )
        self.flag = True  # Module ON/OFF flag
        self.loc = loc[0] if loc else None

        # Create arrays for the attack arguments and cached values
        with self.settrafarrays():
            # Attack type
            self.type = np.array([], dtype="<U20")
            self.params = []
            self.updateflag = np.array([], dtype=bool)

            # Owns ADS-B In
            self.adsbin = ADSBin()
            # Owns ADS-B Out
            self.adsbout = ADSBout()

    def create(self, n: int = 1) -> None:
        """
        When new aircraft are created, they are appended with new fields that store
        the cyber-attack parameters.
        """

        super().create(n)

        # Default to no attacks
        self.type[-n:] = "NONE"
        self.params[-n:] = [SimpleNamespace() for _ in range(n)]
        self.updateflag[-n:] = False

    # --------------------------------------------------------------------
    #                      ATTACKS
    # --------------------------------------------------------------------

    def intercept(self, msgs, index: int, t: float) -> Transmission:
        """
        Overwrites the ADS-B messages depending on the attack.
        """

        type = self.type[index]

        # Call respective attack
        if type == "FREEZE":
            return self.freeze(msgs, index, t)
        elif type == "HIDE":
            return self.hide(msgs, t)
        elif type == "JUMP":
            return self.jump(msgs, index, t)
        else:
            # Return the message as is
            return Transmission(msgs=msgs, source_loc=self.loc, time=0.0)

    def eavesdrop(self, msgs, i: int) -> None:
        """
        Simulate passive attacks to update attacker's ADS-B In cache.
        """

        # Unless GHOST, update ADS-B In cache
        if not self.type[i] == "GHOST":

            # Decode assuming plaintext, skipping CRC checks
            self.adsbin.decode_plaintext(msgs, i_rx=0, i_ac=i)

            # If new aircraft, register ICAO and callsign
            if not self.adsbout.icao[i]:
                self.adsbout.icao[i] = self.adsbin.icao[i]
                self.adsbout.callsign[i] = self.adsbin.callsign[i]

    def freeze(self, msgs, index: int, t: float) -> Transmission:
        """
        Simulate replay attacks by freezing ADS-B outputs to last known values.
        """

        # If initialized
        if self.params[index].init:
            # Overwrite the ADS-B messages with frozen ones
            msgs.position_even = self.params[index].pos_even
            msgs.position_odd = self.params[index].pos_odd
        else:
            # Save last known ADS-B messages
            if msgs.position_even and msgs.position_odd:
                self.params[index].pos_even = msgs.position_even
                self.params[index].pos_odd = msgs.position_odd
                # Set initialization flag to True
                self.params[index].init = True

        # Processing time, preamble + icao
        t += 30e-6

        return Transmission(msgs=msgs, source_loc=self.loc, time=t)

    def hide(self, msgs, t: float) -> Transmission:
        """
        Simulate selective jamming by deleting ADS-B messages.
        """

        # Delete current messages
        msgs.position_even = [""]
        msgs.position_odd = [""]
        msgs.identification = [""]
        msgs.velocity = [""]

        # Processing time, preamble + icao
        t += 30e-6

        return Transmission(msgs=msgs, source_loc=self.loc, time=t)

    def jump(self, msgs, index: int, t: float) -> Transmission:
        """
        Simulate a spoofing attack that modifies ADS-B position.
        """

        # Update ADS-B Out registry with ADS-B In data
        self.adsbout.update_registry(reference=self.adsbin, index=index)

        # Modify registry with spoofed position
        self.adsbout.lat[index] = self.adsbout.lat[index] + self.params[index].lat
        self.adsbout.lon[index] = self.adsbout.lon[index] + self.params[index].lon
        self.adsbout.alt[index] = self.adsbout.alt[index] + self.params[index].alt
        self.adsbout.altGNSS[index] = self.adsbout.alt[index]

        # Compute corrupted messages
        msgs.position_even = self.adsbout.airborne_position(index, even=True)
        msgs.position_odd = self.adsbout.airborne_position(index, even=False)

        # Processing time, half msg
        t += 60e-6

        return Transmission(msgs=msgs, source_loc=self.loc, time=0.0)

    def emit_ghost(self, index: int) -> Transmission:
        """
        Simulate ghost aircraft.
        """

        # Computes ADS-B messages
        return Transmission(
            msgs=self.adsbout.encode_msgs(index),
            source_loc=self.loc,
            time=0.0,
        )

    def cre_ghost(
        self,
        callsign: str,
        lat: float,
        lon: float,
        hdg: float,
        alt: str,
        gs: float,
    ) -> None:
        """
        Inizializes GHOST aircraft.
        """

        # Ghost just created, take last index
        index = -1

        # Assign callsign
        self.adsbout.callsign[index] = callsign

        # All existing callsigns except the new ghost
        callsign_list = self.adsbout.callsign[:-1]
        # If callsign exists, also copy ICAO
        if callsign in callsign_list:
            matching_index = callsign_list.index(callsign)
            self.adsbout.icao[index] = self.adsbout.icao[matching_index]
        else:
            # Otherwise random ICAO
            self.adsbout.icao[index] = f"{np.random.randint(0, 0xFFFFFF+1):06X}"

        # Limit longitude to [-180.0, 180.0]
        lon = ((lon + 180.0) % 360.0) - 180.0

        # Initialize ADS-B Out registry
        rads = np.deg2rad(hdg)
        self.adsbout.alt[index] = alt
        self.adsbout.altGNSS[index] = alt
        self.adsbout.lat[index] = lat
        self.adsbout.lon[index] = lon
        self.adsbout.gs[index] = gs
        self.adsbout.gsnorth[index] = gs * np.cos(rads)
        self.adsbout.gseast[index] = gs * np.sin(rads)
        self.adsbout.trk[index] = hdg
        self.adsbout.vs[index] = 0

    def empty_aircraft_attributes(self, index: int) -> None:
        """
        Set all aircraft traf attributes to np.nan.
        """

        # Set all ghost standard attributes to np.nan
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
                    var[index] = np.nan

    def update_attacks(self) -> None:
        """
        Update the ADS-B Out registry for dynamical attacks.
        """

        # Find airctraft that need updating
        mask = self.updateflag
        if not np.any(mask):
            return  # Nothing to update

        # Move in time simdt
        self.adsbout.alt[mask] = np.round(
            self.adsbout.alt[mask] + self.adsbout.vs[mask] * sim.simdt, 6
        )
        self.adsbout.lat[mask] = self.adsbout.lat[mask] + np.degrees(
            sim.simdt * self.adsbout.gsnorth[mask] / Rearth
        )
        coslat = np.cos(np.deg2rad(self.adsbout.lat[mask]))
        self.adsbout.lon[mask] = self.adsbout.lon[mask] + np.degrees(
            sim.simdt * self.adsbout.gseast[mask] / (coslat * Rearth)
        )
        self.adsbout.altGNSS[mask] = self.adsbout.alt[mask]

    # --------------------------------------------------------------------
    #                      STACK FUNCTIONS
    # --------------------------------------------------------------------

    @stack.commandgroup(name="ATTACK", brief="ATTACK commands")
    def attack(self) -> tuple[bool, str]:
        """
        Cyber-attack related commands.
        """

        return True, (f"ATTACK command\nPossible subcommands: {self.command_str}.")

    @attack.subcommand(name="FREEZE", brief="FREEZE acid")
    def attack_freeze(self, acid: "acid") -> tuple[bool, str]:  # type: ignore
        """
        FREEZE attack for a given aircraft.
        """

        self.type[acid] = "FREEZE"
        self.params[acid].init = False  # Initialization flag

        return True, f"{traf.id[acid]} is under FREEZE attack."

    @attack.subcommand(name="HIDE", brief="HIDE acid")
    def attack_hide(self, acid: "acid") -> tuple[bool, str]:  # type: ignore
        """
        HIDE attack for a given aircraft.
        """

        self.type[acid] = "HIDE"

        return True, f"{traf.id[acid]} is under HIDE attack."

    @attack.subcommand(name="JUMP", brief="JUMP acid,lat-diff,lon-diff,alt-diff")
    def attack_jump(self, acid: "acid", lat: float, lon: float, alt: str) -> tuple[bool, str]:  # type: ignore
        """
        JUMP attack for a given aircraft.
        """

        self.type[acid] = "JUMP"
        self.params[acid] = SimpleNamespace(lat=lat, lon=lon, alt=txt2alt(alt))

        return True, f"{traf.id[acid]} is under JUMP attack."

    @attack.subcommand(name="GHOST", brief="GHOST acid,lat,lon,hdg,alt,spd")
    def attack_ghost(
        self, callsign: str, lat: float, lon: float, hdg: float, alt: str, gs: float
    ) -> tuple[bool, str]:
        """
        GHOST aircraft.
        """

        # If callsign already exists, create a new ACID
        # TODO: check if acid already exists
        if callsign in traf.id:
            acid = (
                chr(np.random.randint(65, 91))
                + chr(np.random.randint(65, 91))
                + "{:>05}"
            )
            acid = acid.format(0)
        else:
            acid = callsign

        # Create new fake aircraft
        traf.cre(  # type:ignore
            acid,
            actype="",
            aclat=0.0,
            aclon=0.0,
            achdg=0.0,
            acalt=0.0,
            acspd=0.0,
        )

        self.empty_aircraft_attributes(index=-1)

        # Set attack type
        self.type[-1] = "GHOST"
        self.updateflag[-1] = True

        # Initialize ADS-B Out registry
        self.cre_ghost(callsign, lat, lon, hdg, alt, gs)

        return True, f"GHOST aircraft created."

    @attack.subcommand(name="MGHOST", brief="MGHOST num")
    def attack_mghost(self, num: int) -> tuple[bool, str]:
        """
        Creates multiple random GHOST aircraft.
        """

        area = ref.area.bbox  # type:ignore

        for _ in range(num):
            # Generate random data
            id = (
                chr(np.random.randint(65, 91))
                + chr(np.random.randint(65, 91))
                + "{:>05}"
            )
            callsign = id.format(0)
            lat = np.random.rand() * (area[2] - area[0]) + area[0]
            lon = np.random.rand() * (area[3] - area[1]) + area[1]
            hdg = np.random.randint(1, 360)
            alt = str(np.random.randint(700, 18000))
            gs = np.random.randint(250, 450)

            # Create ghost aircraft
            self.attack_ghost(callsign, lat, lon, hdg, alt, gs)

        return True, f"{num} GHOST aircraft created."

    @stack.command(name="DELGHOST", brief="DELGHOST [acid]")
    def remove_ghost(self, acid: str = "") -> tuple[bool, str]:  # type: ignore
        """
        Remove selected aircraft. If no ACID is provided, remove ALL ghost aircraft instead.
        """

        if acid != "":
            i = id2idx(acid)
            if i == -1:
                return False, "Aircraft does not exists."
            elif self.type[i] != "GHOST":  # type:ignore
                return False, f"{acid} is not a GHOST aircraft."
            else:
                traf.delete(i)  # type:ignore
                return True, "GHOST removed."
        else:
            mask = self.type == "GHOST"
            indices = np.where(mask)[0]

            for i in sorted(indices, reverse=True):
                traf.delete(i)  # type:ignore
            return True, "All GHOST removed."

    @attack.subcommand(name="NONE", brief="NONE acid")
    def attack_none(self, acid: "acid") -> tuple[bool, str]:  # type: ignore
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
        self.params[acid] = SimpleNamespace()

        return True, f"{traf.id[acid]} is not under attack."

    @attack.subcommand(name="STATUS", brief="STATUS acid")
    def attack_status(self, acid: "acid") -> tuple[bool, str]:  # type: ignore
        """
        Show current attack status for a given aircraft.
        """

        return (
            True,
            f"Aircraft {traf.id[acid]} is currently under {self.type[acid]} attack.",
        )

    @attack.subcommand(name="RESET")
    def attack_reset(self) -> tuple[bool, str]:
        """
        Remove all attacks.
        """

        # Remove ghost aircraft
        self.remove_ghost()

        # Set all other aircraft to NONE
        for idx, _ in enumerate(self.type):
            self.attack_none(idx)

        return (
            True,
            f"All aircraft returned to NONE status.",
        )

    @attack.subcommand(name="TOGGLE", brief="TOGGLE [flag]")
    def attack_on(self, flag: str = "") -> tuple[bool, str]:
        """
        Enable/disable module.
        """

        if flag == "":
            # No argument: flip current state
            self.flag = not self.flag
        else:
            f = flag.lower()
            if f == "true":
                self.flag = True
            elif f == "false":
                self.flag = False
            else:
                return False, "Flag must be 'true' or 'false'."

        state = "ON" if self.flag else "OFF"
        return True, f"Attack module {state}."

    @attack.subcommand(
        name="GHOSTCONF", brief="GHOSTCONF acid,targetacid,dpsi,cpa,tlosh"
    )
    def ghost_confs(self, acid: str, targetidx: "acid", dpsi: float, dcpa: float, tlosh: float) -> None:  # type: ignore
        """
        Create a GHOST aircraft in conflict with target aircraft.

        Arguments:
        - acid: callsign of new aircraft
        - targetid: id of target aircraft
        - dpsi: Conflict angle (angle between tracks of ownship and intruder) (deg)
        - cpa: Predicted distance at closest point of approach (NM)
        - tlosh: Horizontal time to loss of separation ((hh:mm:)sec)
        """

        # Select source
        source = self.adsbout if self.type[targetidx] == "GHOST" else self.adsbin

        # Get data
        data = source.get(targetidx, rx_idx=0)

        # Use the data
        latref = data["lat"]  # [deg]
        lonref = data["lon"]  # [deg]
        altref = data["alt"]  # [m]
        trkref = np.deg2rad(data["trk"])  # [deg] -> [rad]
        gsref = data["gs"]  # [m/s]
        cpa = dcpa * nm
        pzr = settings.asas_pzr * nm
        trk = trkref + np.deg2rad(dpsi)

        # Fix altitude
        acalt = altref
        gsn, gse = gsref * np.cos(trk), gsref * np.sin(trk)

        # Horizontal relative velocity vector
        vreln, vrele = gsref * np.cos(trkref) - gsn, gsref * np.sin(trkref) - gse
        # Relative velocity magnitude
        vrel = np.sqrt(vreln * vreln + vrele * vrele)
        # Relative travel distance to closest point of approach
        drelcpa = tlosh * vrel + (0 if cpa > pzr else np.sqrt(pzr * pzr - cpa * cpa))
        # Initial intruder distance
        dist = np.sqrt(drelcpa * drelcpa + cpa * cpa)
        # Rotation matrix diagonal and cross elements for distance vector
        rd = drelcpa / dist
        rx = cpa / dist
        # Rotate relative velocity vector to obtain intruder bearing
        brn = np.degrees(np.arctan2(-rx * vreln + rd * vrele, rd * vreln + rx * vrele))

        # Calculate intruder lat/lon
        aclat, aclon = tools.geo.kwikpos(latref, lonref, brn, dist / nm)  # type:ignore
        acspd = np.sqrt(gsn * gsn + gse * gse)
        achdg = np.degrees(trk)

        # Create conflicting GHOST aircraft
        self.attack_ghost(acid, aclat, aclon, achdg, str(acalt), acspd)
