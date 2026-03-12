import numpy as np
from types import SimpleNamespace
from bluesky import core, stack, settings, tools, traf, ref, sim
from bluesky.tools.aero import Rearth, nm, kts
from bluesky.tools.misc import txt2alt
from bluesky.plugins.SIMCOM.tools import id2idx
from bluesky.plugins.SIMCOM.adsbout import ADSBout, Transmission
from bluesky.plugins.SIMCOM.adsbin import ADSBin

"""
Module that implements cyber-attacks on the ADS-B protocol.
"""


class Attacker(core.Entity):
    """
    Class that implements Cyber-attackers for ADS-B traffic.
    """

    def __init__(self) -> None:
        super().__init__()

        # List of implemented commands
        self.command_str = (
            "FREEZE, HIDE, JUMP, MGHOST, GHOST, CONFGHOST, NONE, STATUS, RESET, TOGGLE"
        )
        # Module ON/OFF flag
        self.flag = True
        # Spatial references
        self.area = []
        self.loc = None
        self.n = 1

        # Create arrays for the attack arguments and cached values
        with self.settrafarrays():
            # Attack type and parameters
            self.type = np.array([], dtype="<U20")
            self.params = []
            self.updateflag = np.array([], dtype=bool)

            # Owns ADS-B In
            self.adsbin = ADSBin()
            # Owns ADS-B Out
            self.adsbout = ADSBout()

    def create(self, n: int = 1) -> None:
        """
        Initialize attacker parameters for newly created aircraft.
        """

        super().create(n)

        # Default to no attacks
        self.type[-n:] = "NONE"
        self.params[-n:] = [SimpleNamespace() for _ in range(n)]
        self.updateflag[-n:] = False

    def clear_fields(self, index: int) -> None:
        """
        Reset attack fields.
        """

        self.type[index] = "NONE"
        self.params[index] = SimpleNamespace()
        self.updateflag[index] = False

    # --------------------------------------------------------------------
    #                      ATTACKS
    # --------------------------------------------------------------------

    def eavesdrop(self, msg: list, msg_type: str, i: int) -> None:
        """
        Simulate passive attacks to update attacker's ADS-B In cache.
        """

        # Decode assuming plaintext, skipping CRC checks
        self.adsbin.decode_plaintext(msg[0], msg_type, i_rx=0, i_ac=i)

        # Update timer
        setattr(self.adsbin.lastreceived[i][0], msg_type, sim.simt)

        # If new aircraft, save ICAO and callsign in ADS-B Out registry
        if not self.adsbout.icao[i]:
            self.adsbout.icao[i] = self.adsbin.icao[i]
            self.adsbout.callsign[i] = self.adsbin.callsign[i]

    def intercept(
        self, msg: list, msg_type: str, t: float, index: int
    ) -> Transmission | None:
        """
        Apply the attack logic for a given attacker on an outgoing ADS-B message.

        Depending on the attacker type at `index`, the function may modify, drop,
        or replace the original message. If no attack is applied, or if the message
        is missing or of the wrong type, returns None to indicate that no new
        transmission occurs.
        """

        type = self.type[index]

        # Call respective attack
        if type == "FREEZE":
            return self.freeze(msg, msg_type, t, index)
        elif type == "HIDE":
            return self.hide(t)
        elif type == "JUMP":
            return self.jump(msg_type, t, index)
        else:
            # Fallback case: Do nothing
            print(f"Attack {type} not implemented.")
            return None

    def freeze(self, msg: list, msg_type: str, t: float, index: int) -> Transmission:
        """
        Model a replay (freeze) attack by re-transmitting the last observed ADS-B
        message of a given type without updating its content.

        The attacker caches the first valid message and subsequently replays it,
        including all associated metadata (e.g., tag, nonce, or auxiliary fields),
        assuming these are observable and reusable (e.g., via phase overlay). A
        fixed processing is added before emitting the forged Transmission.
        """

        # Ensure the attribute exists
        if not hasattr(self.params[index], msg_type):
            setattr(self.params[index], msg_type, [])

        cached = getattr(self.params[index], msg_type)

        # If not initialized, cache message
        if not cached and msg[0]:
            cached = msg.copy()
            setattr(self.params[index], msg_type, cached)

        # Processing time, df + ca + icao
        t += 32e-6

        return Transmission(msg=cached, source_loc=self.loc, time=t)

    def hide(self, t: float) -> Transmission:
        """
        Model selective jamming by replacing a valid ADS-B message with an
        unintelligible payload.

        The returned message is set to [""] to represent that a transmission
        occurred but was corrupted (e.g., jammed). This differs from returning
        None, which indicates that no transmission took place at all. A fixed
        processing delay is added before emission.
        """

        # Delete message
        msg = [""]
        # Processing time, df + ca + icao
        t += 32e-6

        return Transmission(msg=msg, source_loc=self.loc, time=t)

    def jump(self, msg_type: str, t: float, index: int) -> Transmission | None:
        """
        Simulate a spoofing attack that modifies ADS-B position.
        """

        # Only spoof position messages
        if msg_type in ("even", "odd"):

            # Update ADS-B Out registry with ADS-B In reference
            self.adsbout.update_registry(reference=self.adsbin, index=index)

            # Apply spoofed offsets
            self.adsbout.lat[index] += self.params[index].lat
            self.adsbout.lon[index] += self.params[index].lon
            self.adsbout.alt[index] += self.params[index].alt
            self.adsbout.altGNSS[index] = self.adsbout.alt[index]

            # Recompute only the required frame
            msg = self.adsbout.encode_msg(index, msg_type)

            # Processing delay (half message processing)
            t += 60e-6

            return Transmission(msg=msg, source_loc=self.loc, time=t)

    def emit_msg(self, index: int, msg_type: str) -> Transmission:
        """
        Simulate GHOST aircraft.
        """

        # Create ADS-B message
        msg = self.adsbout.encode_msg(index, msg_type)

        # Update emission timer
        setattr(self.adsbout.lastemit[index], msg_type, sim.simt)

        # Transmit ADS-B message
        return Transmission(
            msg=msg,
            source_loc=self.loc,
            time=0.0,
        )

    def create_ghost(
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
        self.adsbout.alt[index] = txt2alt(alt)
        self.adsbout.altGNSS[index] = self.adsbout.alt[index]
        self.adsbout.lat[index] = lat
        self.adsbout.lon[index] = lon
        self.adsbout.gs[index] = gs * kts
        self.adsbout.gsnorth[index] = gs * np.cos(rads) * kts
        self.adsbout.gseast[index] = gs * np.sin(rads) * kts
        self.adsbout.trk[index] = hdg
        self.adsbout.vs[index] = 0

    def empty_aircraft_attributes(self, index: int) -> None:
        """
        Set all traf attributes to np.nan for given aircraft.
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

    def update(self) -> None:
        """
        Update the ADS-B Out registry for dynamical attacks.
        """

        if not self.flag:
            return

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

        if not self.flag:
            return False, f"The attack module if OFF."

        self.clear_fields(acid)

        self.type[acid] = "FREEZE"

        return True, f"{traf.id[acid]} is under FREEZE attack."

    @attack.subcommand(name="HIDE", brief="HIDE acid")
    def attack_hide(self, acid: "acid") -> tuple[bool, str]:  # type: ignore
        """
        HIDE attack for a given aircraft.
        """

        if not self.flag:
            return False, f"The attack module if OFF."

        self.clear_fields(acid)

        self.type[acid] = "HIDE"

        return True, f"{traf.id[acid]} is under HIDE attack."

    @attack.subcommand(name="JUMP", brief="JUMP acid,lat-diff,lon-diff,alt-diff")
    def attack_jump(self, acid: "acid", lat: float, lon: float, alt: str) -> tuple[bool, str]:  # type: ignore
        """
        JUMP attack for a given aircraft.
        """

        if not self.flag:
            return False, f"The attack module if OFF."

        self.clear_fields(acid)

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

        if not self.flag:
            return False, f"The attack module if OFF."

        # If callsign already exists, create a new ACID
        if callsign in traf.id:
            acid = callsign
            while acid in traf.id:
                acid = (
                    chr(np.random.randint(65, 91))
                    + chr(np.random.randint(65, 91))
                    + f"{np.random.randint(0, 100000):05}"
                )
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
        self.create_ghost(callsign, lat, lon, hdg, alt, gs)

        return True, f"GHOST aircraft created."

    @attack.subcommand(name="MGHOST", brief="MGHOST num")
    def attack_mghost(self, num: int) -> tuple[bool, str]:
        """
        Creates multiple random GHOST aircraft.
        """

        if not self.flag:
            return False, f"The attack module if OFF."

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
        Remove selected GHOST. If no ACID is provided, remove all GHOST aircraft instead.
        """

        if not self.flag:
            return False, f"The attack module if OFF."

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

        if not self.flag:
            return False, f"The attack module if OFF."

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

        if not self.flag:
            return False, f"The attack module if OFF."

        return (
            True,
            f"Aircraft {traf.id[acid]} is currently under {self.type[acid]} attack.",
        )

    @attack.subcommand(name="RESET")
    def attack_reset(self) -> tuple[bool, str]:
        """
        Remove all attacks.
        """

        if not self.flag:
            return False, f"The attack module if OFF."

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
    def attack_toggle(self, flag: str = "") -> tuple[bool, str]:
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
    def ghost_confs(self, acid: str, targetidx: "acid", dpsi: float, dcpa: float, tlosh: float) -> tuple | None:  # type: ignore
        """
        Create a GHOST aircraft in conflict with target aircraft.

        Arguments:
        - acid: callsign of new aircraft
        - targetid: id of target aircraft
        - dpsi: Conflict angle (angle between tracks of ownship and intruder) (deg)
        - cpa: Predicted distance at closest point of approach (NM)
        - tlosh: Horizontal time to loss of separation ((hh:mm:)sec)
        """

        if not self.flag:
            return False, f"The attack module if OFF."

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
