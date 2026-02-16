from dataclasses import dataclass
from typing import Any
from bluesky.tools import areafilter
from bluesky.tools.geo import kwikdist
from bluesky.tools.aero import nm
from bluesky import core, settings, stack

"""
This module should implement transmission noise effects.
"""

settings.set_variable_defaults(attacker_locations=[], receiver_locations=[])

C = 299702547  # speed of light in air [m/s]


@dataclass
class Transmission:
    msgs: Any
    source_loc: tuple[float, float] | None
    time: float = 0.0  # Time of emission
    # power?


class PhysicalLayer(core.Entity):
    """
    Manages the physical layer aspects (detection, signal noise) of the ADS-B protocol.
    """

    def __init__(self):
        super().__init__()

        self.load_loc()

    def reset(self) -> None:
        """
        On reset, load locations again.
        """

        self.load_loc()

    def select_msgs(self, received: list) -> str | None:
        """
        Given the list of received messages, select one to be decoded.
        """

        if not received:
            return None  # Defensive fallback

        # First element is assumed to be the original transmission
        original_msg, t_orig = received[0]

        msg_time = 112e-6  # 112 microseconds

        # All others are spoofed
        for msgs, t in received[1:]:
            if msgs is None:
                continue
            # Time constraint on attacker
            if abs(t - t_orig) < msg_time:
                return msgs

        # If no spoofed message arrived, return original
        return original_msg

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
                areafilter.defineArea(
                    name=area_name,
                    shape="CIRCLE",
                    coordinates=coords,
                )
                areas.append(areafilter.basic_shapes[area_name])

            elif len(coords) > 6 and len(coords) % 2 == 0:
                areafilter.defineArea(
                    name=area_name,
                    shape="POLY",
                    coordinates=coords,
                )
                areas.append(areafilter.basic_shapes[area_name])

            else:
                print(f"Given '{name_prefix}' areas are not valid.")

        return areas

    def propagate(self, transmission, receiver: str, index: int = 0) -> tuple:
        """
        Compute the transmission losses along the path and returns a new message with timestamp.

        If messages fail to arrive it returns None instead.
        """

        # Select range flag
        ranges = self.atk_ranges if receiver == "atk" else self.rx_ranges

        # Copy message to propagate
        msgs = transmission.msgs.copy()

        # If ranges do not matter, return original message
        if not ranges:
            return msgs, 0.0

        # Gather receiver and emitter locations
        receiver_area = (
            self.attackers[index] if receiver == "atk" else self.receivers[index]
        )
        receiver_loc = self.atk_loc[index] if receiver == "atk" else self.rx_loc[index]

        lat_tx, lon_tx = transmission.source_loc
        lat_rx, lon_rx = receiver_loc

        # Check coverage
        if not receiver_area.checkInside(lat_tx, lon_tx, 0):
            return None, None

        # Distance in m
        d = kwikdist(lat_tx, lon_tx, lat_rx, lon_rx) * nm

        # Apply noise model
        msgs = self.noise_model(d, msgs)

        # Time of arrival
        t_arrival = transmission.time + d / C

        # Retun message and timestamp
        return msgs, t_arrival

    def noise_model(self, d: float, msgs):
        """
        Implement a noise model.
        """

        return msgs

    def hide(self, target: str = "") -> None:
        """
        Hide target areas.
        """

        if target == "ATK":
            areas = self.attackers
        elif target == "RX":
            areas = self.receivers
        else:
            areas = self.attackers + self.receivers

        for area in areas:
            areafilter.colour(area.name, 0, 0, 0)

    def show(self, target: str = "") -> None:
        """
        Show target areas.
        """

        configs = []

        if target in ("ATK", ""):
            configs.append((self.attackers, (255, 0, 0)))

        if target in ("RX", ""):
            configs.append((self.receivers, (0, 200, 155)))

        for areas, color in configs:
            for area in areas:
                areafilter.colour(area.name, *color)

    def highlight(self, rx: int = 0) -> None:
        """
        Highlight a given receiver on screen.
        """

        self.hide("RX")
        areafilter.colour(f"RX{rx}", 0, 255, 200)

    @staticmethod
    def center(shape: areafilter.Shape) -> tuple[float, float]:
        """Return the geographic center of a shape.

        - For Circle: returns the center coordinates.
        - For Poly: returns the midpoint of the bounding box.
        """
        if isinstance(shape, areafilter.Circle):
            return (shape.clat, shape.clon)
        elif isinstance(shape, areafilter.Poly):
            clat = 0.5 * (shape.bbox[0] + shape.bbox[2])
            clon = 0.5 * (shape.bbox[1] + shape.bbox[3])
            return (clat, clon)
        elif isinstance(shape, areafilter.Box):
            clat = 0.5 * (shape.bbox[0] + shape.bbox[2])
            clon = 0.5 * (shape.bbox[1] + shape.bbox[3])
            return (clat, clon)
        else:
            raise TypeError(f"Unsupported shape type: {type(shape)}")

    @stack.command(name="LOADLOC", brief="LOADLOC")
    def load_loc(self) -> tuple[bool, str]:
        """
        Load attackers and receivers locations from settings.
        """

        # Read geometric locations
        self.attackers = self._parse_areas(settings.attacker_locations, "ATK")
        self.receivers = self._parse_areas(settings.receiver_locations, "RX")

        # Store center locations
        self.atk_loc = []
        for atk in self.attackers:
            self.atk_loc.append(self.center(atk))

        self.rx_loc = []
        for rx in self.receivers:
            self.rx_loc.append(self.center(rx))

        # How many entities
        self.n_rx = max(1, len(self.receivers))
        self.n_atk = max(1, len(self.attackers))

        # Enable receivers' and attackers' ranges
        self.rx_ranges = len(self.receivers) != 0
        self.atk_ranges = len(self.attackers) != 0

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
        N = self.n_rx

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
            return False, f"{rx} is not a valid number."
