import numpy as np
import csv
from math import *
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from bluesky import (
    core,
    stack,
    traf,
    sim,
    settings,
    tools,
)  # , settings, navdb, sim, scr, tools
from bluesky.network.publisher import state_publisher
from bluesky.tools.aero import ft, nm, kts
import bluesky.plugins.SIMCOM.adsb_encoder as encoder
from bluesky.plugins.SIMCOM.adsb_attacks import ADSBattacks
from bluesky.plugins.SIMCOM.adsb_statebased import ConflictDetection
from bluesky.plugins.SIMCOM.shared_airspace import SharedAirspace
from bluesky.plugins.SIMCOM.adsb_logger import ADSBlog
from bluesky.plugins.SIMCOM.adsb_security import ADSBsecurity
from bluesky.plugins.SIMCOM.adsb_receivers import ADSBreceivers

"""
SIMCOM plugin that implements the ADS-B protocol.

TODO:
- Add white Gaussian noise (GNSS noise) to the position/velocity readings.
- Refactor update to work with Timers.
- [Refactor @publisher to work with Signals inside update.]
- Move everything inside BlueSky, not a plugin anymore [reset GHOSTS at each update].
- Create ADS-B based conflict resolution method.
- Network-level structure: add attacker and receiver node positions.
- Consider splitting traffic.py into aircraft.py (contains the FMS data and sharedair, security is passed on init) and traffic.py (more abstract, contains security, aircraft, receivers, noise).
"""

ACUPDATE_RATE = 5  # Update rate of aircraft update messages [Hz]
ADSB_UPDATE = 0.5  # Update dt for ADS-B messages [s]
LOG_UPDATE = 1  # Update dt for LOG [s]


def init_plugin():
    """
    Plugin initialisation function.
    """

    print("SIMCOM: Loading ADS-B plugin...")

    # Instantiate singleton entity
    adsbtraffic = ADSBtraffic()

    # Configuration parameters
    config = {
        "plugin_name": "ADSBTRAFFIC",
        "plugin_type": "sim",
        # The update function is called after traffic is updated.
        "update": adsbtraffic.update,
        # "preupdate": adsbtraffic.preupdate,
        # Reset contest
        "reset": adsbtraffic.reset,
    }

    return config


# List of data for each message type
@dataclass
class ADSBmessages:
    position_even: list = field(default_factory=lambda: [""])
    position_odd: list = field(default_factory=lambda: [""])
    identification: list = field(default_factory=lambda: [""])
    velocity: list = field(default_factory=lambda: [""])


class ADSBtraffic(core.Entity):
    """
    Main SIMCOM class. It defines the ADS-B attributes and methods.
    """

    def __init__(self) -> None:
        """
        Initialize the ADSBTraffic class.
        """

        super().__init__()

        # Data logging variables
        self.log = SimpleNamespace(flag=False)
        # Manual timer for CD and CR
        self.asastimer = core.Timer(name="adsb_asas", dt=settings.asas_dt)

        # Aircraft-defined ADS-B quantities
        with self.settrafarrays():
            self.icao = []
            self.callsign = []  # identifier (string)
            self.altGNSS = np.array([], dtype=float)  # [m]
            self.altbaro = np.array([], dtype=float)  # [m]
            self.lat = np.array([], dtype=float)  # latitude [deg]
            self.lon = np.array([], dtype=float)  # longitude [deg]
            self.gsnorth = np.array([], dtype=float)  # ground speed [m/s]
            self.gseast = np.array([], dtype=float)  # ground speed [m/s]
            self.gs = np.array([], dtype=float)  # ground speed [m/s]
            self.vs = np.array([], dtype=float)  # vertical speed [m/s]
            self.trk = np.array([], dtype=float)  # track angle [deg]
            self.capability = []  # CA field
            self.ss = []  # surveillance status

            # ADS-B messages
            self.msgs = []

            # Global traffic entities
            self.cd = ConflictDetection()
            self.security = ADSBsecurity()
            self.attacks = ADSBattacks()
            self.sharedair = SharedAirspace()
            self.receivers = ADSBreceivers(security=self.security)

    def create(self, n: int = 1) -> None:
        """
        This function gets called automatically
        when new aircraft are created.
        """

        super().create(n)
        # The childrens attacks and sharedair are created automatically

        # Inizialize the ICAO addresses and callsign
        icaos = np.array(
            [f"{x:06X}" for x in np.random.randint(0, 0xFFFFFF + 1, size=n)]
        )
        self.icao[-n:] = icaos
        self.callsign[-n:] = traf.id[-n:]

        # Initialize ADS-B flight data (FMS)
        noise = np.random.uniform(-150, 150, size=n)
        self.altGNSS[-n:] = np.maximum(traf.alt[-n:] + noise, 0)
        self.altbaro[-n:] = traf.alt[-n:]
        self.lat[-n:] = traf.lat[-n:]
        self.lon[-n:] = traf.lon[-n:]
        self.gsnorth[-n:] = traf.gsnorth[-n:]  # type:ignore
        self.gseast[-n:] = traf.gseast[-n:]  # type:ignore
        self.gs[-n:] = traf.gs[-n:]
        self.vs[-n:] = traf.vs[-n:]
        self.trk[-n:] = traf.trk[-n:]

        # CA = 5, 'aircraft with level 2 transponder, airborne'.
        self.capability[-n:] = [5] * n
        self.ss[-n:] = [0] * n  # Surveillance status

        # Initialize empty ADS-B messages
        self.msgs[-n:] = [ADSBmessages() for _ in range(n)]

    @core.timed_function(
        dt=ADSB_UPDATE, hook="update"  # type:ignore
    )  # Run every 0.5 simulated seconds
    def ADSB_update(self) -> None:
        """
        The ADS-B data are updated based on the actual AC data, except for
        GHOST aircraft.
        """

        # GHOST aircraft cannot use real data to update ADS-B fields
        mask = self.attacks.type != "GHOST"
        indices = np.where(mask)[0]

        # Aircraft update their ADS-B value from their actual values
        # TODO: Add noise
        self.lat[mask] = traf.lat[mask]
        self.lon[mask] = traf.lon[mask]
        noise = np.random.uniform(-150, 150, size=len(indices))
        self.altbaro[mask] = traf.alt[mask]
        self.altGNSS[mask] = np.maximum(traf.alt[mask] + noise, 0)
        self.gsnorth[mask] = traf.gsnorth[mask]  # type:ignore
        self.gseast[mask] = traf.gseast[mask]  # type:ignore
        self.vs[mask] = traf.vs[mask]

        # Compute ADS-B messages for all aircraft
        for i in range(traf.ntraf):
            if self.security.flag and self.security.scheme[i] != "NONE":
                # Compute messages and apply scheme
                self.msgs[i].position_odd = encoder.airborne_position(
                    self, i, False, crc=False
                )
                self.msgs[i].position_even = encoder.airborne_position(
                    self, i, True, crc=False
                )
                self.msgs[i].identification = encoder.identification(self, i, crc=False)
                self.msgs[i].velocity = encoder.airborne_velocity(self, i, crc=False)

                # Apply cybersecurity schemes
                self.security.apply_schemes(self.msgs[i], i)
            else:
                # Otherwise plaintext ADS-B message
                self.msgs[i].position_odd = encoder.airborne_position(self, i, False)
                self.msgs[i].position_even = encoder.airborne_position(self, i, True)
                self.msgs[i].identification = encoder.identification(self, i)
                self.msgs[i].velocity = encoder.airborne_velocity(self, i)

            # Cyber-attacks
            if self.attacks.flag:
                self.attacks.apply_attacks(self.msgs, i)
            else:
                # Remove ghosts
                if self.attacks.type[i] == "GHOST":
                    self.attacks.arg[i]["init"] = 0  # Prepare for initialization
                    self.msgs[i].position_odd = None
                    self.msgs[i].position_even = None
                    self.msgs[i].identification = None
                    self.msgs[i].velocity = None

            self.receivers.decode(self.msgs[i], i)

    def reset(self) -> None:
        """
        Clear all traffic data upon simulation reset.
        """

        # Remove GHOST aircraft
        stack.stack("DELGHOST")
        # Some child reset functions depend on a correct value of self.ntraf
        traf.ntraf = 0
        # This ensures that the traffic arrays (which size is dynamic)
        super().reset()

    def update(self) -> None:
        """
        Update functions is called every sim.dt step.
        """

        # Conflict detection update
        # If instead I pass traf, self (with some minor changes)
        # I can simulate the airborne CD
        if self.asastimer.readynext:
            self.cd.update(self, self)

        # Update GHOST position
        if self.attacks.flag:
            self.attacks.update_ghosts()

    # --------------------------------------------------------------------
    #                      PUBLISHER
    # --------------------------------------------------------------------

    @state_publisher(topic="ADSBDATA", dt=1000 // ACUPDATE_RATE)
    def send_ADSB_data(self):
        """Broadcast ADS-B data to the GPU for displaying.
        The rate is higher than in real world, so it includes dead reckoning.

        The id is to keep track of AC.
        """

        data = dict()

        # Ground-truth data
        data["id"] = traf.id
        data["gt_lon"] = traf.lon
        data["gt_lat"] = traf.lat
        data["translvl"] = traf.translvl

        # Receiver ADS-B In data
        data["icao"] = self.receivers.icao
        data["callsign"] = self.receivers.callsign
        data["ss"] = self.receivers.ss
        data["lat"] = self.receivers.lat
        data["lon"] = self.receivers.lon
        data["alt"] = self.receivers.alt
        data["gs"] = self.receivers.gs
        data["vs"] = self.receivers.vs
        data["trk"] = self.receivers.trk

        # Conflict detection data
        data["rpz"] = self.cd.rpz
        data["inconf"] = self.cd.inconf
        data["tcpamax"] = self.cd.tcpamax

        return data

    # --------------------------------------------------------------------
    #                      STACK COMMANDS
    # --------------------------------------------------------------------

    @stack.command(name="SSTATUS", brief="SSTATUS acid,[status (0, 1, 2)]")
    def sstatus(self, acid: "acid", status: str = ""):  # type: ignore
        """
        Set the surveillance status of a given aircraft.
        If the status is not given, it returns the status of the aircraft.
        """

        # If no status, return current status
        if status == "":
            return (
                True,
                f"Aircraft {traf.id[acid]} surveillance status is {self.ss[acid]}.",
            )

        # Otherwise apply passed status
        self.ss[acid] = int(status)

        return True, f"The surveillance status for {traf.id[acid]} is set to {status}."

    @stack.command(name="ADSBLOG", brief="ADSBLOG fname")
    def data_logger(self, fname: str = "SIMCOM"):  # TODO: remove!
        """
        Create the CSV file for the logging.
        """

        # If the flag is False, create file and enable logging
        if not self.log.flag:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            fname = f"{fname}_{timestamp}.csv"
            self.log.fname = f"output/{fname}"

            with open(self.log.fname, mode="a", newline="") as f:
                writer = csv.writer(f)
                # Write header only once
                writer.writerow(
                    [
                        "sim.t",
                        "traf.id",
                        "traf.lat",
                        "traf.lon",
                        "traf.alt",
                        "traf.gsnorth",
                        "traf.gseast",
                        "traf.perf.fuelflow",
                        "adsb.callsign",
                        "adsb.lat",
                        "adsb.lon",
                        "adsb.alt",
                        "adsb.gsnorth",
                        "adsb.gseast",
                        "adsb.msg",
                        "adsb.attack.type",
                        "adsb.sharedair.role",
                        "adsb.cd.confpairs",
                        "adsb.cd.dcpa",
                    ]
                )

            # Enable logging
            self.log.flag = True
            return True, f"Saving data in {fname}..."
        else:
            # If the flag is True, stop logging
            self.log.flag = False
            self.log.fname = ""
            return True, "Data logging has stopped."

    @core.timed_function(dt=LOG_UPDATE)
    def log_data(self) -> None:
        """
        Logs the data in log.fname every LOG_UPDATE seconds.
        """

        # If logging is enabled, save a new row every dt seconds
        if self.log.flag:
            with open(self.log.fname, mode="a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        sim.simt,
                        traf.id,
                        traf.lat,
                        traf.lon,
                        traf.alt,
                        traf.gsnorth,  # type:ignore
                        traf.gseast,  # type:ignore
                        traf.perf.fuelflow,  # type:ignore
                        self.callsign,
                        self.lat,
                        self.lon,
                        self.altbaro,
                        self.gsnorth,
                        self.gseast,
                        self.msg,
                        self.attacks.type,
                        self.sharedair.role,
                        self.cd.confpairs,
                        self.cd.dcpa,
                    ]
                )
        else:
            return

    @stack.command(name="GHOSTCONF", brief="GHOSTCONF acid,targetacid,dpsi,cpa,tlosh")
    def ghost_confs(self, acid, targetidx: "acid", dpsi: float, dcpa: float, tlosh: float) -> None:  # type: ignore
        """
        Create a GHOST aircraft in conflict with target aircraft.

        Arguments:
        - acid: callsign of new aircraft
        - targetid: id of target aircraft
        - dpsi: Conflict angle (angle between tracks of ownship and intruder) (deg)
        - cpa: Predicted distance at closest point of approach (NM)
        - tlosh: Horizontal time to loss of separation ((hh:mm:)sec)
        """

        # Not in adsb_attacks because it needs access to ADS-B data.
        latref = self.lat[targetidx]  # [deg]
        lonref = self.lon[targetidx]  # [deg]
        altref = self.altbaro[targetidx]  # [m]
        trkref = radians(self.trk[targetidx])  # [deg]->[rad]
        gsref = self.gs[targetidx]  # [m/s]
        cpa = dcpa * nm
        pzr = settings.asas_pzr * nm
        trk = trkref + radians(dpsi)

        acalt = altref
        gsn, gse = gsref * cos(trk), gsref * sin(trk)

        # Horizontal relative velocity vector
        vreln, vrele = gsref * cos(trkref) - gsn, gsref * sin(trkref) - gse
        # Relative velocity magnitude
        vrel = sqrt(vreln * vreln + vrele * vrele)
        # Relative travel distance to closest point of approach
        drelcpa = tlosh * vrel + (0 if cpa > pzr else sqrt(pzr * pzr - cpa * cpa))
        # Initial intruder distance
        dist = sqrt(drelcpa * drelcpa + cpa * cpa)
        # Rotation matrix diagonal and cross elements for distance vector
        rd = drelcpa / dist
        rx = cpa / dist
        # Rotate relative velocity vector to obtain intruder bearing
        brn = degrees(atan2(-rx * vreln + rd * vrele, rd * vreln + rx * vrele))

        # Calculate intruder lat/lon
        aclat, aclon = tools.geo.kwikpos(latref, lonref, brn, dist / nm)  # type:ignore
        acspd = sqrt(gsn * gsn + gse * gse)
        achdg = degrees(trk)

        # Create conflicting GHOST aircraft
        self.attacks.attack_ghost(
            acid, aclat, aclon, achdg, str(acalt / ft), acspd / kts
        )
