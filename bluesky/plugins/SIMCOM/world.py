import numpy as np
from dataclasses import fields
from bluesky import core, settings, stack, traf, sim
from bluesky.tools.geo import kwikdist
from bluesky.tools.aero import nm
from bluesky.plugins.SIMCOM.aircraft import Aircraft
from bluesky.plugins.SIMCOM.attacker import Attacker
from bluesky.plugins.SIMCOM.receivers import Receivers
from bluesky.plugins.SIMCOM.tools import defineArea, colour, basic_shapes

"""
This module implement transmission noise effects, while orchestrating all the agents.
"""

settings.set_variable_defaults(attacker_locations=[], receiver_locations=[])

C = 299702547  # speed of light in air [m/s]


class World(core.Entity):
    """
    Manages the world physics and the actors (aircraft, attacker and receivers).
    """

    def __init__(self, security):
        super().__init__()

        # Reference to security
        self.security = security

        # Define the various actors
        with self.settrafarrays():
            # Pass reference to security
            self.aircraft = Aircraft(security=security)
            self.receivers = Receivers(security=security)

            # Attacker cannot access security layer
            self.attacker = Attacker()

        # Load locations from settings
        self.load_loc()

    @property
    def rx_view(self):
        """
        Return current receiver view, index 0-padded.
        """

        return self.view - 1 if self.rx_ranges else 0

    def _parse_areas(self, data: list, name_prefix: str) -> list:
        """
        Convert a list of coordinates into area items (Circles or Poly).
        Returns a list of area objects.
        """

        if len(data) == 0:
            return []

        areas = []
        for idx, coords in enumerate(data, start=1):
            area_name = name_prefix + str(idx)

            if len(coords) == 3:
                defineArea(
                    name=area_name,
                    shape="CIRCLE",
                    coordinates=coords,
                )
                areas.append(basic_shapes[area_name])

            elif len(coords) > 6 and len(coords) % 2 == 0:
                defineArea(
                    name=area_name,
                    shape="POLY",
                    coordinates=coords,
                )
                areas.append(basic_shapes[area_name])

            else:
                print(f"Given '{name_prefix}' areas are not valid.")

        return areas

    # --------------------------------------------------------------------
    #                      UPDATE LOOPS
    # --------------------------------------------------------------------

    def update(self):
        """
        Execute one simulation-step update of the world communication layer.

        Real aircraft generate ADS-B transmissions, while ghost aircraft (if the
        attacker is active) may inject forged messages. This models
        the discrete-time communication cycle at the BlueSky timestep resolution,
        separating genuine traffic emission from adversarial injection.
        """

        # Extract indices for real and ghost AC
        N = traf.ntraf

        real_indices = [i for i in range(N) if self.is_real(i)]
        ghost_indices = [i for i in range(N) if self.is_ghost(i) and self.attacker.flag]

        # Reset traf attributes
        self.reset_traf(ghost_indices)

        # ADS-B message loops
        self.adsb_cycle(real_indices)
        self.inject_ghost_transmission(ghost_indices)

    def reset(self):
        """
        On reset, relaod locations again.
        """

        self.load_loc()

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

    def adsb_cycle(self, real_indices: list):
        """
        Update cycle for real aircraft ADS-B transmissions within a single timestep.

        Each aircraft may emit messages for all ADS-B types based on configured
        frequencies. Propagation times are computed and enforced (e.g., for attacker
        reachability), but we assume that all propagation occurs within the current
        timestep because propagation delays are much smaller than the timestep
        duration. Attackers may eavesdrop or modify messages before they reach
        receivers, which then decode and update their caches. Stale data is cleared
        at the end of the cycle.
        """

        # Default update frequencies of ADS-B messages per type
        frequencies = self.aircraft.adsbout.freq

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
                        msg, t = self.propagate(transmissions[0], "atk")

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
                    for i_rx in range(self.receivers.n):
                        received = []

                        # Loop over all transmissions
                        for transmission in transmissions:
                            # Propagate transmission from souce to receiver
                            msg, t = self.propagate(transmission, "rx", index=i_rx)
                            if msg:
                                received.append((msg, t))

                        # Of all the valid messages arrived, select one to decode
                        msg = self.select_msg(received)

                        # If at least a message arrived, decode
                        if msg is not None:
                            self.receivers.decode(msg, msg_type, i_rx, i_ac)

                        # At the end of the loop, each receiver clear old cache
                        self.receivers.clear_stale_cache(i_ac, i_rx)

    def inject_ghost_transmission(self, ghost_indices: list) -> None:
        """
        Update cycle for ghost (attacker-injected) ADS-B transmissions within a single timestep.

        Ghost aircraft messages are emitted according to configured frequencies and
        propagated to receivers, where they are decoded just like real aircraft
        transmissions. Propagation times are computed and applied, but all are
        assumed to occur within the current timestep, reflecting the approximation
        that message delays are much smaller than the simulation timestep. Stale
        receiver data is cleared at the end of the cycle.
        """

        # Default update frequencies of ADS-B messages per type
        frequencies = self.attacker.adsbout.freq

        for i_ac in ghost_indices:
            # Gather timers of last emitted messages
            lastemit = self.attacker.adsbout.lastemit[i_ac]

            # Loop over all message types
            for f in fields(lastemit):
                msg_type = f.name
                last_time = getattr(lastemit, msg_type)
                dt = getattr(frequencies, msg_type)

                # If last message is far in the past, emit a new one
                if last_time + dt <= sim.simt:
                    transmissions = [self.attacker.emit_msg(i_ac, msg_type)]

                    # For each receiver
                    for i_rx in range(self.receivers.n):
                        received = []

                        # Loop over all transmissions
                        for transmission in transmissions:
                            # Propagate transmission from souce to receiver
                            msg, t = self.propagate(transmission, "rx", i_rx)
                            if msg:
                                received.append((msg, t))

                        # Of all the valid messages arrived, select one to decode
                        msg = self.select_msg(received)

                        # If at least a message arrived, decode
                        if msg is not None:
                            self.receivers.decode(msg, msg_type, i_rx, i_ac)

                        # At the end of the loop, each receiver clear old cache
                        self.receivers.clear_stale_cache(i_ac, i_rx)

    def reset_traf(self, indices: list[int]) -> None:
        """
        Wrapper function that reset the traf attributes to np.nan.
        """

        for i in indices:
            if not np.isnan(traf.lat[i]):
                self.attacker.empty_aircraft_attributes(i)

    def under_attack(self, i: int) -> bool:
        """
        Return True if aircraft is under attack.
        """

        return self.attacker.type[i] != "NONE"

    # --------------------------------------------------------------------
    #                      MESSAGE PROPAGATION
    # --------------------------------------------------------------------

    def propagate(
        self, transmission, receiver: str, index: int = 0
    ) -> tuple[list | None, float]:
        """
        Model the propagation of a transmission from a source to a receiver.

        If range checks are disabled or the source location is missing, the message
        is assumed to propagate instantly and without loss. Otherwise, propagation
        distance is computed, noise is applied, and the arrival time is delayed
        according to the distance divided by the speed of light. If the receiver is
        out of coverage, None is returned to indicate the message did not arrive.
        """

        # Determine ranges
        ranges = self.atk_ranges if receiver == "atk" else self.rx_ranges

        # Copy message to propagate
        msg = transmission.msg.copy()

        # If ranges do not matter, return original message
        if not ranges:
            return msg, 0.0

        # If no source location, return original message
        if not transmission.source_loc:
            return msg, 0.0

        # Determine receiver area and location
        if receiver == "atk":
            receiver_area = self.attacker.area[index]
            receiver_loc = self.attacker.area[index].loc
        else:
            receiver_area = self.receivers.area[index]
            receiver_loc = self.receivers.area[index].loc

        lat_tx, lon_tx = transmission.source_loc
        lat_rx, lon_rx = receiver_loc

        # Check coverage
        if not receiver_area.checkInside(lat_tx, lon_tx, 0):
            return None, 0.0

        # Distance [m]
        d = kwikdist(lat_tx, lon_tx, lat_rx, lon_rx) * nm

        # Apply noise model
        msg = self.noise_model(d, msg)

        # Time of arrival
        t_arrival = transmission.time + d / C

        # Retun message and timestamp
        return msg, t_arrival

    def select_msg(self, received: list) -> list | None:
        """
        Model the antenna-level selection when multiple overlapping messages arrive.

        Physically, an antenna may pick up several transmissions with partial
        overlap. This function chooses which message is actually received and
        decoded: the first message is considered legitimate, while others
        (potentially spoofed) are only selected if they arrive within a short
        window (112 µs). Returns None if no messages were received.
        """

        if not received:
            return None  # Defensive fallback

        # First element is assumed to be the original transmission
        original_msg, t_orig = received[0]

        msg_time = 112e-6  # 112 microseconds

        # All others are spoofed
        for msg, t in received[1:]:
            if msg is None:
                continue
            # Message must arrive before end of original message
            if t < t_orig + msg_time:
                return msg

        # If no spoofed message arrived, return original
        return original_msg

    def noise_model(self, d: float, msg):
        """
        Implement a noise model.
        """

        return msg

    # --------------------------------------------------------------------
    #                      AREAS AND GRAPHICS
    # --------------------------------------------------------------------

    def hide(self, target: str = "") -> None:
        """
        Hide target areas.
        """

        if target == "ATK":
            areas = self.attacker.area
        elif target == "RX":
            areas = self.receivers.area
        else:
            areas = self.attacker.area + self.receivers.area

        for area in areas:
            colour(area.name, 0, 0, 0)

    def show(self, target: str = "") -> None:
        """
        Show target areas.
        """

        configs = []

        if target in ("ATK", ""):
            configs.append((self.attacker.area, (255, 0, 0)))

        if target in ("RX", ""):
            configs.append((self.receivers.area, (0, 200, 155)))

        for areas, color in configs:
            for area in areas:
                colour(area.name, *color)

    def highlight(self, rx: int = 0) -> None:
        """
        Highlight a given receiver on screen.
        """

        self.hide("RX")
        colour(f"RX{rx}", 0, 255, 200)

    # --------------------------------------------------------------------
    #                      STACK COMMANDS
    # --------------------------------------------------------------------

    @stack.command(name="LOADLOC", brief="LOADLOC")
    def load_loc(self) -> tuple[bool, str]:
        """
        Load attackers and receivers locations from settings.
        """

        # Read geometric locations
        self.attacker.area = self._parse_areas(settings.attacker_locations, "ATK")
        self.receivers.area = self._parse_areas(settings.receiver_locations, "RX")

        # How many entities
        self.attacker.n = max(1, len(self.attacker.area))
        self.receivers.n = max(1, len(self.receivers.area))

        # Enable receivers' and attackers' ranges
        self.rx_ranges = len(self.receivers.area) != 0
        self.atk_ranges = len(self.attacker.area) != 0

        self.attacker.loc = self.attacker.area[0].loc if self.atk_ranges else None

        self.view = 1 if self.rx_ranges else 0

        # Set colors
        self.highlight(self.view)
        self.show("ATK")

        return True, ""

    @stack.command(name="RXVIEW", brief="RXVIEW 0/1/2/...")
    def rxview(self, rx: int) -> tuple[bool, str]:
        """
        Select the receiver view.
        """

        # Number of physical receivers
        N = self.receivers.n

        # Select receiver POV
        if 1 <= rx <= N:
            # Highlight selected RX
            self.rx_ranges = True
            self.view = rx
            self.highlight(rx)
            return True, ""

        # Select god-view (RX have infinite ranges)
        elif rx == 0:
            # Highlight no RX
            self.rx_ranges = False
            self.view = 0
            self.highlight()
            return True, ""

        # Not a valid POV
        else:
            return False, f"{rx} is not a valid receiver number."

    @stack.command(name="ATKRANGE", brief="ATKRANGE 0/1")
    def atkrange(self, atk: int) -> tuple[bool, str]:
        """
        Toggle ATK ranges.
        """

        if atk == 0:
            self.atk_ranges = False
            self.attacker.loc = None
            self.hide("ATK")
            return True, "Attacker range disabled."
        elif atk == 1 and self.attacker.area:
            self.atk_ranges = True
            self.attacker.loc = self.attacker.area[0].loc
            self.show("ATK")
            return True, "Attacker range enabled."
        else:
            return False, f"{atk} is not a valid number."
