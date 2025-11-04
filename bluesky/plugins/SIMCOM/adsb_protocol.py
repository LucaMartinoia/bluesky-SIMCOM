import numpy as np
import csv
from math import *
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
from bluesky.plugins.SIMCOM.adsb_encoder import (
    ADSB_identification,
    ADSB_position,
    ADSB_velocity,
)
from bluesky.plugins.SIMCOM.adsb_attacks import ADSBattacks
from bluesky.plugins.SIMCOM.adsb_statebased import ConflictDetection
from bluesky.plugins.SIMCOM.shared_airspace import SharedAirspace

"""SIMCOM plugin that implements the ADS-B protocol."""

ACUPDATE_RATE = 5  # Update rate of aircraft update messages [Hz]
ADSB_UPDATE = 0.5  # Update dt for ADS-B messages [s]
LOG_UPDATE = 1  # Update dt for LOG [s]


def init_plugin():
    """Plugin initialisation function."""

    print("SIMCOM: Loading ADS-B protocol plugin...")

    # Instantiate singleton entity
    adsbprotocol = ADSBprotocol()

    # Configuration parameters
    config = {
        "plugin_name": "ADSBPROTOCOL",
        "plugin_type": "sim",
        # The update function is called after traffic is updated.
        "update": adsbprotocol.update,
        "preupdate": adsbprotocol.preupdate,
        # Reset contest
        "reset": adsbprotocol.reset,
    }

    return config


# Need some way to still identify AC uniquely:
# the ACID still remains the main identifier.
class ADSBprotocol(core.Entity):
    """Main SIMCOM class. It defines the ADS-B attributes and methods."""

    def __init__(self):
        """Initialize the ADSB protocol class."""

        super().__init__()
        # Data logging variables
        self.log = SimpleNamespace(flag=False)
        # Manual timer for CD and CR
        self.asastimer = core.Timer(name="adsb_asas", dt=settings.asas_dt)

        # All classes deriving from Entity can register lists and numpy arrays
        # that hold per-aircraft data. This way, their size is automatically
        # updated when aircraft are created or deleted in the simulation.
        with self.settrafarrays():
            self.icao = np.array([], dtype="U6")
            self.callsign = []  # identifier (string)
            self.altGNSS = np.array([], dtype=float)  # [m]
            self.altbaro = np.array([], dtype=float)  # [m]
            self.lat = np.array([])  # latitude [deg]
            self.lon = np.array([])  # longitude [deg]
            self.gsnorth = np.array([])  # ground speed [m/s]
            self.gseast = np.array([])  # ground speed [m/s]
            self.gs = np.array([])  # ground speed [m/s]
            self.vs = np.array([])  # vertical speed [m/s]
            self.trk = np.array([])  # track angle [deg]
            self.capability = np.array([], dtype=int)  # CA field
            self.ss = np.array([], dtype=int)  # surveillance status

            # ADS-B messages
            self.msg_pos_o = np.array([], dtype="<U28")
            self.msg_pos_e = np.array([], dtype="<U28")
            self.msg_id = np.array([], dtype="<U28")
            self.msg_v = np.array([], dtype="<U28")

            # Global traffic entities
            self.cd = ConflictDetection()
            self.attacks = ADSBattacks()
            self.sharedair = SharedAirspace()

    def create(self, n=1):
        """This function gets called automatically
        when new aircraft are created."""

        super().create(n)
        # The childrens attacks and sharedair are created automatically

        # Inizialize the ICAO addresses and callsign
        icaos = np.array(
            [f"{x:06X}" for x in np.random.randint(0, 0xFFFFFF + 1, size=n)]
        )
        self.icao[-n:] = icaos
        self.callsign[-n:] = traf.id[-n:]

        # Initialize ADS-B flight data
        noise = np.random.uniform(-150, 150, size=n)
        self.altGNSS[-n:] = np.maximum(traf.alt[-n:] + noise, 0)
        self.altbaro[-n:] = traf.alt[-n:]
        self.lat[-n:] = traf.lat[-n:]
        self.lon[-n:] = traf.lon[-n:]
        self.gsnorth[-n:] = traf.gsnorth[-n:]
        self.gseast[-n:] = traf.gseast[-n:]
        self.gs[-n:] = traf.gs[-n:]
        self.vs[-n:] = traf.vs[-n:]
        self.trk[-n:] = traf.trk[-n:]

        # CA = 5, 'aircraft with level 2 transponder, airborne'.
        self.capability[-n:] = 5
        self.ss[-n:] = 0  # Surveillance status

        # Initialize ADS-B messages
        for j in range(-n, 0):
            self.msg_pos_o[j] = ADSB_position(self, j, False)
            self.msg_pos_e[j] = ADSB_position(self, j, True)
            self.msg_id[j] = ADSB_identification(self, j)
            self.msg_v[j] = ADSB_velocity(self, j)

    @core.timed_function(
        dt=ADSB_UPDATE, hook="update"
    )  # runs every 0.5 simulated seconds
    def ADSB_update(self):
        """The ADS-B data are updated based on the actual AC data, except for
        GHOST aircraft."""

        n = traf.ntraf

        # GHOST aircraft cannot use real data to update ADS-B fields
        mask = self.attacks.type != "GHOST"

        indices = np.where(mask)[0]

        # Aircraft update their ADS-B value from their actual values
        self.lat[mask] = traf.lat[:n]
        self.lon[mask] = traf.lon[:n]
        noise = np.random.uniform(-150, 150, size=n)
        self.altbaro[mask] = traf.alt[:n]
        self.altGNSS[mask] = np.maximum(traf.alt[:n] + noise, 0)
        self.gsnorth[mask] = traf.gsnorth[:n]
        self.gseast[mask] = traf.gseast[:n]
        self.vs[mask] = traf.vs[:n]

        # Compute ADS-B messages for all aircraft
        for i in indices:
            self.msg_pos_o[i] = ADSB_position(self, i, False)
            self.msg_pos_e[i] = ADSB_position(self, i, True)
            self.msg_id[i] = ADSB_identification(self, i)
            self.msg_v[i] = ADSB_velocity(self, i)

        # Man in the middle attacks
        self.attacks.man_in_the_middle(self)

    def reset(self):
        """Clear all traffic data upon simulation reset."""

        # Remove GHOST aircraft
        stack.stack("DELGHOST")
        # Some child reset functions depend on a correct value of self.ntraf
        traf.ntraf = 0
        # This ensures that the traffic arrays (which size is dynamic)
        super().reset()

    def update(self):
        """Update functions is called every sim.dt step."""

        # Conflict detection update
        # If instead I pass traf, self (with some minor changes)
        # I can simulate the airborne CD
        if self.asastimer.readynext:
            self.cd.update(self, self)

        # Update GHOST position
        self.attacks.update_ghost_pos(self)

    def preupdate(self):
        """Pre-update functions is called every sim.dt step."""

        # Initialize GHOST aircraft
        self.attacks.init_ghosts(self)

    # --------------------------------------------------------------------
    #                      PUBLISHER
    # --------------------------------------------------------------------

    @state_publisher(topic="ADSBDATA", dt=1000 // ACUPDATE_RATE)
    def send_ADSB_data(self):
        """Broadcast ADS-B data to the GPU for displaying.
        The rate is higher than in real world, so it includes dead reckoning.

        The id is to keep track of AC. The status is not necessary in theory,
        but pyModeS cannot extract the field from ADS-B messages.
        """

        data = dict()

        # Aircraft metadata
        data["id"] = traf.id
        data["status"] = self.ss
        data["attack"] = self.attacks.type

        # Conflict detection data
        data["rpz"] = self.cd.rpz
        data["inconf"] = self.cd.inconf
        data["tcpamax"] = self.cd.tcpamax

        # ADS-B messages
        data["ADSBmsg_pos_o"] = self.msg_pos_o
        data["ADSBmsg_pos_e"] = self.msg_pos_e
        data["ADSBmsg_id"] = self.msg_id
        data["ADSBmsg_v"] = self.msg_v

        return data

    # --------------------------------------------------------------------
    #                      STACK COMMANDS
    # --------------------------------------------------------------------

    @stack.command(name="SSTATUS", brief="SSTATUS acid,[status (0, 1, 2)]")
    def sstatus(self, acid: "acid", status: str = ""):  # type: ignore
        """Set the surveillance status of a given aircraft.
        If the status is not given, it returns the status of the aircraft."""

        if status == "":
            return (
                True,
                f"Aircraft {traf.id[acid]} surveillance status is {self.ss[acid]}.",
            )

        self.ss[acid] = int(status)

        return True, f"The surveillance status for {traf.id[acid]} is set to {status}."

    @stack.command(name="ADSBLOG", brief="ADSBLOG fname")
    def data_logger(self, fname: str = "SIMCOM"):
        """Create the CSV file for the logging."""

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
                        "adsb.callsign",
                        "adsb.lat",
                        "adsb.lon",
                        "adsb.alt",
                        "adsb.gsnorth",
                        "asdb.gseast",
                        "adsb.msg_pos_o",
                        "adsb.msg_pos_e",
                        "adsb.msg_v",
                        "adsb.msg_id",
                        "adsb.attack.type",
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
    def log_data(self):
        """Logs the data in log.fname every LOG_UPDATE seconds."""

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
                        traf.gsnorth,
                        traf.gseast,
                        self.callsign,
                        self.lat,
                        self.lon,
                        self.altbaro,
                        self.gsnorth,
                        self.gseast,
                        self.msg_pos_o,
                        self.msg_pos_e,
                        self.msg_v,
                        self.msg_id,
                        self.attacks.type,
                    ]
                )
        else:
            return

    @stack.command(name="GHOSTCONF", brief="GHOSTCONF acid,targetacid,dpsi,cpa,tlosh")
    def ghost_confs(self, acid, targetidx: "acid", dpsi: float, dcpa: float, tlosh: float):  # type: ignore
        """Create a GHOST aircraft in conflict with target aircraft.

        Arguments:
        - acid: callsign of new aircraft
        - targetid: id of target aircraft
        - dpsi: Conflict angle (angle between tracks of ownship and intruder) (deg)
        - cpa: Predicted distance at closest point of approach (NM)
        - tlosh: Horizontal time to loss of separation ((hh:mm:)sec)
        """

        # Not in adsb_attacks because it needs access to ADAS-B data.
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
        aclat, aclon = tools.geo.kwikpos(latref, lonref, brn, dist / nm)
        acspd = sqrt(gsn * gsn + gse * gse)
        achdg = degrees(trk)

        # Create conflicting GHOST aircraft
        self.attacks.attack_ghost(
            acid, aclat, aclon, achdg, str(acalt / ft), acspd / kts
        )
