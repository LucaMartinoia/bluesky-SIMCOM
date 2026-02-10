import numpy as np
from bluesky import core, stack, traf, settings
from bluesky.network.publisher import state_publisher
from bluesky.plugins.SIMCOM.attacker import Attacker
from bluesky.plugins.SIMCOM.conflict_detection import ConflictDetection
from bluesky.plugins.SIMCOM.security import Security
from bluesky.plugins.SIMCOM.receivers import Receivers
from bluesky.plugins.SIMCOM.aircraft import Aircraft
from bluesky.plugins.SIMCOM.datalogger import Logger
from bluesky.plugins.SIMCOM.physical_layer import PhysicalLayer
from bluesky.tools import areafilter

"""
SIMCOM is a BlueSky plugin that adds ADS-B-specific functionality, providing tools to analyze
the impact of cyber-attacks and evaluate cyber-security measures in air traffic systems.

TODO:
- Network-level structure: add attacker and receiver node positions.
- Add white Gaussian noise (GNSS noise) to the position/velocity readings.
- Implement other attack types (surveillance, icao).
- Refactor update to work with Timers (change Nonce management and add even/odd message caching in ADS-B In).
- Move everything inside BlueSky, not a plugin anymore.
- Create ADS-B based conflict resolution method.
- Fix bugs and refactor ADS-B encoding.
- Add toggle to move detection from ground to aircraft (TCAS-like)


Consider removing Entity from Receivers and Attackers (not Singletons) and allow core.py to own multiple instances of
Attackers and Receivers, each with its own ADS-B In/Out. In particular, civilRecevier and militaryReceiver.
"""

ACUPDATE_RATE = 2  # Update rate of aircraft update messages [Hz]
UPDATE = 0.5  # Update dt for ADS-B messages [s]

settings.set_variable_defaults(log_update=1)


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

        # Timers for CD and CR
        self.asastimer = core.Timer(name="adsb_asas", dt=settings.asas_dt)

        self.logger = Logger()
        self.phys_layer = PhysicalLayer()

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

        # Precompute the detection mask
        # atk_mask, rx_mask = self.phys_layer.detection_masks()

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
                    if not np.isnan(traf.lat[i]):
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

        # Attack and spoofing-related
        data["attack"] = self.receivers.detatk
        data["spoofing"] = self.receivers.spoofing_map

        # Conflict detection data
        data["rpz"] = self.cd.rpz
        data["inconf"] = self.cd.inconf
        data["tcpamax"] = self.cd.tcpamax

        return data

    @core.timed_function(dt=settings.log_update)
    def logging(self):
        """
        Save data.
        """

        self.logger.logging(self.attacker, self.receivers, self.cd)
