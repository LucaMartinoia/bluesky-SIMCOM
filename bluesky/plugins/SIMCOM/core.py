import numpy as np
from dataclasses import fields
from bluesky import core, stack, traf, settings, sim
from bluesky.network.publisher import state_publisher
from bluesky.plugins.SIMCOM.attacker import Attacker
from bluesky.plugins.SIMCOM.conflict_detection import ConflictDetection
from bluesky.plugins.SIMCOM.security import Security
from bluesky.plugins.SIMCOM.receivers import Receivers
from bluesky.plugins.SIMCOM.aircraft import Aircraft
from bluesky.plugins.SIMCOM.datalogger import Logger
from bluesky.plugins.SIMCOM.physical_layer import PhysicalLayer
from bluesky.plugins.SIMCOM.adsbout import ADSBout

"""
SIMCOM is a BlueSky plugin that adds ADS-B-specific functionality, providing tools to analyze
the impact of cyber-attacks and evaluate cyber-security measures in air traffic systems.

TODO:
- Fix bugs and add more descriptions.
- Implement other attack types (surveillance, icao).
"""

ACUPDATE_RATE = 2  # Update rate of aircraft update messages [Hz]
UPDATE = 0.5  # Update dt for ADS-B messages [s]

settings.set_variable_defaults(log_update=1)


def init_plugin():
    """
    Plugin initialisation function.
    """

    print("\nSIMCOM PLUGIN LOADED.\n")

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
            self.receivers = Receivers(security=self.security, loc=self.phys)

            # Attacker cannot access security layer
            self.attacker = Attacker(loc=self.phys)

    def create(self, n: int = 1) -> None:
        """
        This function gets called automatically when new aircraft are created.
        """

        # The childrens are created automatically
        super().create(n)

    def update(self) -> None:
        """
        Iterates over all aircraft to compute ADS-B messages, apply attacks and decode.

        Passing the aircraft index and the message type along.
        """

        N = traf.ntraf

        # Extract indices for real and ghost AC
        real_indices = [i for i in range(N) if self.is_real(i)]
        ghost_indices = [i for i in range(N) if self.is_ghost(i) and self.attacker.flag]

        # ADS-B message loops
        self.adsb_cycle(real_indices)
        self.inject_ghost_transmission(ghost_indices)

        # Ground-based conflict detection update
        if self.asastimer.readynext:
            i_rx = self.phys.rx_view_idx
            self.cd.update(self.receivers.adsbin, self.receivers.adsbin, i_rx)

        # Update GHOST position
        if self.attacker.flag:
            self.attacker.update()

    def adsb_cycle(self, real_indices: list):
        """
        Real aircraft ADS-B update cycle.
        """

        # Default update frequencies of ADS-B messages per type
        frequencies = ADSBout.freq

        for i_ac in real_indices:
            # Gather timers of last emitted messages
            lastemit = self.aircraft.last_emit(i_ac)

            # Loop over all message types
            for f in fields(lastemit):
                msg_type = f.name
                last_time = getattr(lastemit, msg_type)
                dt = getattr(frequencies, msg_type)

                # If last message is far in the past, emit a new one
                if last_time + dt <= sim.simt:
                    transmissions = [self.aircraft.emit_msg(i_ac, msg_type)]

                    # Apply attacks
                    if self.attacker.flag:
                        # Propagate message to attacker
                        msg, t = self.phys.propagate(transmissions[0], "atk")

                        # If message arrives
                        if msg is not None:
                            # Perform passive attack
                            self.attacker.eavesdrop(msg, msg_type, i_ac)

                            # Perform active attacks
                            if self.under_attack(i_ac):
                                transmission = self.attacker.intercept(
                                    msg,
                                    msg_type,
                                    t,
                                    i_ac,
                                )
                                if transmission:
                                    transmissions.append(transmission)

                    # For each receiver
                    for i_rx in range(self.phys.n_rx):
                        received = []

                        # Loop over all transmissions
                        for transmission in transmissions:
                            # Propagate transmission from souce to receiver
                            msg, t = self.phys.propagate(transmission, "rx", index=i_rx)
                            if msg:
                                received.append((msg, t))

                        # Of all the valid messages arrived, select one to decode
                        msg = self.phys.select_msg(received)

                        # If at least a message arrived, decode
                        if msg is not None:
                            self.receivers.decode(msg, msg_type, i_rx, i_ac)

                        # At the end of the loop, each receiver clear old cache
                        self.receivers.clear_stale_cache(i_ac, i_rx)

    def inject_ghost_transmission(self, ghost_indices: list) -> None:
        """
        GHOST aircraft ADS-B update cycle.
        """

        # Default update frequencies of ADS-B messages per type
        frequencies = ADSBout.freq

        for i_ac in ghost_indices:
            # Gather timers of last emitted messages
            lastemit = self.attacker.adsbout.lastemit[i_ac]

            # Reset traf
            self.reset_traf(i_ac)

            # Loop over all message types
            for f in fields(lastemit):
                msg_type = f.name
                last_time = getattr(lastemit, msg_type)
                dt = getattr(frequencies, msg_type)

                # If last message is far in the past, emit a new one
                if last_time + dt <= sim.simt:
                    transmissions = [self.attacker.emit_msg(i_ac, msg_type)]

                    # For each receiver
                    for i_rx in range(self.phys.n_rx):
                        received = []

                        # Loop over all transmissions
                        for transmission in transmissions:
                            # Propagate transmission from souce to receiver
                            msg, t = self.phys.propagate(transmission, "rx", i_rx)
                            if msg:
                                received.append((msg, t))

                        # Of all the valid messages arrived, select one to decode
                        msg = self.phys.select_msg(received)

                        # If at least a message arrived, decode
                        if msg is not None:
                            self.receivers.decode(msg, msg_type, i_rx, i_ac)

                        # At the end of the loop, each receiver clear old cache
                        self.receivers.clear_stale_cache(i_ac, i_rx)

    def is_real(self, i: int) -> bool:
        """
        Return True if aircraft is real, False if a GHOST.
        """

        return self.attacker.type[i] != "GHOST"

    def is_ghost(self, i: int) -> bool:
        """
        Return True if aircraft is GHOST, False if a true.
        """

        return not self.is_real(i)

    def reset_traf(self, i: int) -> None:
        """
        Wrapper function that reset the traf attributes to np.nan.
        """

        if not np.isnan(traf.lat[i]):
            self.attacker.empty_aircraft_attributes(i)

    def under_attack(self, i: int) -> bool:
        """
        Return True if aircraft is under attack.
        """

        return self.attacker.type[i] != "NONE"

    def reset(self) -> None:
        """
        Clear all traffic data upon simulation reset.
        """

        # Remove GHOST aircraft
        stack.stack("DELGHOST")

        # This ensures that the traffic arrays (which size is dynamic)
        super().reset()

        self.phys.reset()

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

        # Receiver view data
        data["view"] = self.phys.view
        data["rxranges"] = self.phys.rx_ranges

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
        data["attack"] = self.receivers.atkflag

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
