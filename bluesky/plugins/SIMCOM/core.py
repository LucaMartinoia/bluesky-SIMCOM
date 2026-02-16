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

"""
SIMCOM is a BlueSky plugin that adds ADS-B-specific functionality, providing tools to analyze
the impact of cyber-attacks and evaluate cyber-security measures in air traffic systems.

TODO:
- Refactor update to work with Timers (change Nonce management and add even/odd message caching in ADS-B In).

- Implement other attack types (surveillance, icao).
- Move everything inside BlueSky, not a plugin anymore.
- Create ADS-B based conflict resolution method.
- Create custom GUI for the areas.
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

        # Data logger and physics
        self.logger = Logger()
        self.phys = PhysicalLayer()

        with self.settrafarrays():
            # Global traffic entities
            self.cd = ConflictDetection()  # Might go into receivers
            self.security = Security()
            # Pass reference to security
            self.aircraft = Aircraft(security=self.security)
            self.receivers = Receivers(security=self.security, loc=self.phys.rx_loc)

            # Attacker cannot access security layer
            self.attacker = Attacker(loc=self.phys.atk_loc)

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
        for i_ac in range(traf.ntraf):

            # All message transmissions for this aircraft
            transmissions = []

            # If real aircraft
            if self.is_real_aircraft(i_ac):

                # Emit ADS-B messages
                transmissions = [self.aircraft.emit_msgs(i_ac)]

                # Apply attacks
                if self.attacker.flag:

                    # Propagate message to attacker
                    msgs, t = self.phys.propagate(transmissions[0], "atk")

                    # Update the attacker's ADS-B In cache
                    if msgs is not None:
                        self.attacker.eavesdrop(msgs, i_ac)

                        # Perform active attacks
                        if self.under_attack(i_ac):
                            transmission = self.attacker.intercept(msgs, i_ac, t)
                            transmissions.append(transmission)

            else:
                if self.attacker.flag:

                    # Empty again GHOST aircraft traffic attributes, just in case
                    if not np.isnan(traf.lat[i_ac]):
                        self.attacker.empty_aircraft_attributes(i_ac)

                    # Emit GHOST ADS-B messages
                    transmissions.append(self.attacker.emit_ghost(i_ac))

            # Receivers decode messages
            for i_rx in range(self.receivers.n_rx):
                received = []

                # Loop over transmissions
                for transmission in transmissions:
                    msgs_rx, t = self.phys.propagate(transmission, "rx", index=i_rx)

                    # Save all received messages
                    received.append((msgs_rx, t))

                # Pick one to decode
                msgs = self.phys.select_msgs(received)

                if msgs is None:
                    self.elapse_time(i_rx, i_ac)
                else:
                    self.receivers.decode(msgs, i_rx, i_ac)

    def is_real_aircraft(self, i: int) -> bool:
        """
        Return True if aircraft is real, False if a GHOST.
        """

        return self.attacker.type[i] != "GHOST"

    def under_attack(self, i: int) -> bool:
        """
        Return True if aircraft is under attack.
        """

        return self.attacker.type[i] != "NONE"

    def elapse_time(self, i_rx, i_ac) -> None:
        """
        Skip time as counters.
        """

        value = 1 + self.receivers.adsbin.stale_counters[i_ac][i_rx].position
        self.receivers.adsbin.set_counters(i_rx, i_ac, value)
        if value >= self.receivers.adsbin.max_counter:
            self.receivers.adsbin.clear_cache(i_rx, i_ac)

    def reset(self) -> None:
        """
        Clear all traffic data upon simulation reset.
        """

        # Remove GHOST aircraft
        stack.stack("DELGHOST")

        # This ensures that the traffic arrays (which size is dynamic)
        super().reset()

        self.phys.reset()

    def update(self) -> None:
        """
        Update functions is called every sim.dt step.
        """

        # Ground-based conflict detection update
        if self.asastimer.readynext:
            i_rx = self.phys.view - 1 if self.phys.rx_ranges else 0
            ownship, intruder = self.cd.gather_data(
                self.receivers.adsbin, self.receivers.adsbin, i_rx
            )
            self.cd.update(ownship, intruder)

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
        data["view"] = self.phys.view
        data["rxranges"] = self.phys.rx_ranges

        # Stack receiver-dependent ADS-B In data
        data["icao"] = self.receivers.adsbin.icao
        data["callsign"] = self.receivers.adsbin.callsign
        data["ss"] = self.receivers.adsbin.ss
        data["lat"] = self.receivers.adsbin.lat
        data["lon"] = self.receivers.adsbin.lon
        data["alt"] = self.receivers.adsbin.alt
        data["gs"] = self.receivers.adsbin.gs
        data["vs"] = self.receivers.adsbin.vs
        data["trk"] = self.receivers.adsbin.trk
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
