import numpy as np
from dataclasses import dataclass
from matplotlib.path import Path
from typing import Any
from bluesky.tools import areafilter
from bluesky.tools.geo import kwikdist
from bluesky.tools.aero import nm
from bluesky import core, settings, stack
from bluesky.network.publisher import StatePublisher

"""
This module should implement transmission noise effects.
"""

settings.set_variable_defaults(attacker_locations=[], receiver_locations=[])

C = 299702547  # speed of light in air [m/s]
# Dictionary of all basic shapes (The shape classes defined in this file) by name
basic_shapes = dict()
# Publisher object to manage publishing of states to clients
polypub = StatePublisher("ADSBPOLY", collect=True)


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
        receiver_loc = (
            (self.attackers[index].clat, self.attackers[index].clon)
            if receiver == "atk"
            else (self.receivers[index].clat, self.receivers[index].clon)
        )

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
            colour(area.name, 0, 0, 0)

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
                colour(area.name, *color)

    def highlight(self, rx: int = 0) -> None:
        """
        Highlight a given receiver on screen.
        """

        self.hide("RX")
        colour(f"RX{rx}", 0, 255, 200)

    @stack.command(name="LOADLOC", brief="LOADLOC")
    def load_loc(self) -> tuple[bool, str]:
        """
        Load attackers and receivers locations from settings.
        """

        # Read geometric locations
        self.attackers = self._parse_areas(settings.attacker_locations, "ATK")
        self.receivers = self._parse_areas(settings.receiver_locations, "RX")

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


@polypub.payload
def puball():
    return dict(polys={name: poly.raw for name, poly in basic_shapes.items()})


def defineArea(name, shape, coordinates, top=1e9, bottom=-1e9):
    """
    Define a new area.
    """
    if name == "LIST":
        if not basic_shapes:
            return True, "No shapes are currently defined."
        else:
            return True, "Currently defined shapes:\n" + ", ".join(basic_shapes)
    if coordinates is None:
        if name in basic_shapes:
            return True, str(basic_shapes[name])
        else:
            return False, f"Unknown shape: {name}"
    elif shape == "CIRCLE":
        basic_shapes[name] = Circle(name, coordinates, top, bottom)
    elif shape[:4] == "POLY":
        basic_shapes[name] = Poly(name, coordinates, top, bottom)

    clat = basic_shapes[name].clat
    clon = basic_shapes[name].clon
    # Pass the shape on to the connected clients
    polypub.send_update(
        polys={name: dict(shape=shape, coordinates=coordinates, clat=clat, clon=clon)}
    )

    return True  # , f'Created {shape} {name}'


def colour(name, r, g, b):
    """
    Set custom color for visual objects.
    """
    poly = basic_shapes.get(name)
    if poly:
        poly.color = (r, g, b)
        polypub.send_update(polys={name: dict(color=poly.color)})
        return True
    return False, "No shape found with name " + name


class Poly(areafilter.Poly):
    """
    A polygon shape with center.
    """

    def __init__(self, name, coordinates, top=1e9, bottom=-1e9):
        super().__init__(name, coordinates, top, bottom)
        self.border = Path(np.reshape(coordinates, (len(coordinates) // 2, 2)))

        self.clat, self.clon = self.center()

    def center(self) -> tuple[float, float]:
        """
        Return the geographic center of a shape.
        """

        clat = 0.5 * (self.bbox[0] + self.bbox[2])
        clon = 0.5 * (self.bbox[1] + self.bbox[3])
        return (clat, clon)

    @property
    def raw(self):
        ret = dict(
            name=self.name,
            shape=self.kind(),
            coordinates=self.coordinates,
            clat=self.clat,
            clon=self.clon,
        )
        if hasattr(self, "color"):
            ret["color"] = self.color
        return ret


class Circle(areafilter.Circle):
    """
    A Circle shape.
    """

    def __init__(self, name, coordinates, top=1e9, bottom=-1e9):
        super().__init__(name, coordinates, top, bottom)

    @property
    def raw(self):
        ret = dict(
            name=self.name,
            shape=self.kind(),
            coordinates=self.coordinates,
            clat=self.clat,
            clon=self.clon,
        )
        if hasattr(self, "color"):
            ret["color"] = self.color
        return ret
