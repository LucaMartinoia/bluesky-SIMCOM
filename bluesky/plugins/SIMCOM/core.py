import csv
from datetime import datetime
from types import SimpleNamespace
from bluesky import core, stack, traf, sim, settings
from bluesky.network.publisher import state_publisher
from bluesky.plugins.SIMCOM.attacker import Attacker
from bluesky.plugins.SIMCOM.conflict_detection import ConflictDetection
from bluesky.plugins.SIMCOM.security import Security
from bluesky.plugins.SIMCOM.receivers import Receivers
from bluesky.plugins.SIMCOM.aircraft import Aircraft

"""
SIMCOM is a BlueSky plugin that adds ADS-B-specific functionality, providing tools to analyze
the impact of cyber-attacks and evaluate cyber-security measures in air traffic systems.

TODO:
- Data Logger.
- Network-level structure: add attacker and receiver node positions.
- Add white Gaussian noise (GNSS noise) to the position/velocity readings.
- Consider changing attack implementation.
- Implement other attack types (surveillance, icao).
- Refactor update to work with Timers (change Nonce management).
- Refactor @publisher to work with Signals inside update.
- Move everything inside BlueSky, not a plugin anymore [reset GHOSTS at each update].
- Create ADS-B based conflict resolution method.
- Fix bugs in ADS-B encoding.
- Add toggle to move detection from ground to aircraft (TCAS-like)
- Modify method to work with data gathered from self.receivers.
- Move ADS-B view to be based on ICAO instead of ACID
- Shown velocity is GS and not CAS
- Publisher for using ICAO instead of traf.id
"""

ACUPDATE_RATE = 2  # Update rate of aircraft update messages [Hz]
UPDATE = 0.5  # Update dt for ADS-B messages [s]
LOG_UPDATE = 1  # Update dt for LOG [s]


def init_plugin():
    """
    Plugin initialisation function.
    """

    print("\nSIMCOM: Loading SIMCOM plugin...\n")

    # Instantiate singleton entity
    adsbtraffic = Traffic()

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

    # --------------------------------------------------------------------
    #                      CORE LOGIC
    # --------------------------------------------------------------------


# Inherit singleton properties from Entity
class Traffic(core.Entity):
    """
    Main SIMCOM class.

    Manages the core logic and owns the various entities (aircraft, attackers, receivers).
    """

    def __init__(self) -> None:
        super().__init__()

        # Data logging variables
        # TODO: Remove
        self.log = SimpleNamespace(flag=False)
        # Timers for CD and CR
        self.asastimer = core.Timer(name="adsb_asas", dt=settings.asas_dt)

        with self.settrafarrays():
            # Global traffic entities
            self.cd = ConflictDetection()  # Might go into receivers
            self.security = Security()
            # Pass reference to security so all share same structure
            self.aircraft = Aircraft(security=self.security)
            self.receivers = Receivers(security=self.security)
            # Attacker cannot access security keys
            self.attacker = Attacker()

    def create(self, n: int = 1) -> None:
        """
        This function gets called automatically when new aircraft are created.
        """

        # The childrens are created automatically
        super().create(n)

    @core.timed_function(dt=UPDATE)  # Run every 0.5 seconds # type:ignore
    def update_ADSB(self) -> None:
        """
        Core logic loop.

        Iterates over all aircraft to compute ADS-B messages, apply attacks and decode.
        """

        # Compute ADS-B messages for all aircraft
        for i in range(traf.ntraf):

            # If true aircraft
            if self.is_real_aircraft(i):

                # Emit ADS-B messages
                msgs = self.aircraft.emit_msgs(i)

                # Apply attacks
                if self.attacker.flag:
                    # Update the ADS-B In attacker cache
                    self.attacker.eavesdrop(msgs, i)
                    # Perform active attacks
                    msgs = self.attacker.intercept(msgs, i)

            else:
                if self.attacker.flag:
                    # Empty again GHOST aircraft traffic attributes, just in case
                    self.attacker.empty_aircraft_attributes(i)
                    # Emit GHOST ADS-B messages
                    msgs = self.attacker.emit_ghost(i)

            # Receivers decode messages
            self.receivers.decode(msgs, i)

    def is_real_aircraft(self, i: int) -> bool:
        """
        Return True if aircraft is real, False if a GHOST.
        """

        return self.attacker.type[i] != "GHOST"

    def reset(self) -> None:
        """
        Clear all traffic data upon simulation reset.
        """

        # Remove GHOST aircraft
        stack.stack("DELGHOST")

        # This ensures that the traffic arrays (which size is dynamic)
        super().reset()

    def update(self) -> None:
        """
        Update functions is called every sim.dt step.
        """

        # Ground-based conflict detection update
        if self.asastimer.readynext:
            self.cd.update(self.receivers.adsbin, self.receivers.adsbin)

        # Update GHOST position
        if self.attacker.flag:
            self.attacker.update_attacks()

    # --------------------------------------------------------------------
    #                      PUBLISHER
    # --------------------------------------------------------------------

    @state_publisher(topic="ADSBDATA", dt=1000 // ACUPDATE_RATE)
    def send_ADSB_data(self):
        """
        Broadcast ADS-B data to the GPU for displaying.

        The id is to keep track of AC.
        """

        data = dict()

        # Ground-truth data (for joining line)
        data["id"] = traf.id
        data["gt_lon"] = traf.lon
        data["gt_lat"] = traf.lat
        data["translvl"] = traf.translvl

        # Receiver ADS-B In data
        data["icao"] = self.receivers.adsbin.icao
        data["callsign"] = self.receivers.adsbin.callsign
        data["ss"] = self.receivers.adsbin.ss
        data["lat"] = self.receivers.adsbin.lat
        data["lon"] = self.receivers.adsbin.lon
        data["alt"] = self.receivers.adsbin.alt
        data["gs"] = self.receivers.adsbin.gs
        data["vs"] = self.receivers.adsbin.vs
        data["trk"] = self.receivers.adsbin.trk

        # Conflict detection data
        data["rpz"] = self.cd.rpz
        data["inconf"] = self.cd.inconf
        data["tcpamax"] = self.cd.tcpamax

        return data

    # --------------------------------------------------------------------
    #                      STACK COMMANDS
    # --------------------------------------------------------------------

    @stack.command(name="ADSBLOG", brief="ADSBLOG fname")
    def data_logger(self, fname: str = "SIMCOM") -> tuple[bool, str]:  # TODO: remove!
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
                        self.attacker.type,
                        self.sharedair.role,
                        self.cd.confpairs,
                        self.cd.dcpa,
                    ]
                )
        else:
            return
