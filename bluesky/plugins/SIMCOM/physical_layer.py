import numpy as np
from bluesky.tools import areafilter
from bluesky import core, settings, traf, stack

"""
This module should implement noise/jitter/lag effect to the ADS-B messages.

Secondly, we can implement in this module delays/packet losses per aircraft. These are of two kind: delays are computed
at the source, these are just random statistical fluctuations that affect when the Timer actually fires a new ADS-B
message. Packet losses instead are due to noise along the path. A simple approach is to apply packet losses right AFTER
the ADS-B messages are computed. This way, we can simply set to "None" messages which are lost.
We could also think about specific bit-flip errors, but probably too advanced for now. Finally, we could also compute the overalps time:
if two messages arrive at a given receiver with a delay of less than 150 micro second at least one of them is dropped.

Finally, we could have a stack function or a setting that defines a position (lat/lon) that represent the physical
position of the receiver. Then, the packet loss rate can be scaled with respect to this position (30%+ and growing),
potentially also including curvature effect and loss of line of sight, so that aircraft which are too far from the
point are really invisible.

The packet losses should be computed PER aircraft and PER receiver. The receiver, for now, are just ground ones, but in
the future we could consider also aircraft receivers. The GUI then "hooks" on a SPECIFIC ground receiver (or acts as a all-knowing entity)
and displays only the messages received by a single receiver.

This way we are simulating a very basic physical+network layer, but without going down to the sub micro-second EM physics and signal processing.

This could also be two module: one that computes the noise and so on (like adsb_encoder computes the ADS-B messages) and another one that
uses these functions and implement the network-level aspects.
"""

settings.set_variable_defaults(attacker_locations=[], receiver_locations=[])


class PhysicalLayer(core.Entity):
    """
    Manages the physical layer aspects (detection, signal noise) of the ADS-B protocol.
    """

    def __init__(self):
        self.attackers = self._parse_areas(settings.attacker_locations, "ATK")
        self.receivers = self._parse_areas(settings.receiver_locations, "RX")

        self.atk_rx_mask = self.atk_rx_detection()

        # Set colors
        for area in self.attackers.values():
            areafilter.colour(area.name, 255, 0, 0)

        for area in self.receivers.values():
            areafilter.colour(area.name, 0, 200, 155)

    def _parse_areas(self, data, name_prefix):
        """
        Convert a list of coordinates into area items (Circles or Poly).
        """

        if len(data) != 0:
            for idx, coords in enumerate(data, start=1):
                if len(coords) == 3:
                    areafilter.defineArea(
                        name=name_prefix + str(idx),
                        shape="CIRCLE",
                        coordinates=coords,
                    )
                elif len(coords) > 6 and len(coords) % 2 == 0:
                    areafilter.defineArea(
                        name=name_prefix + str(idx),
                        shape="POLY",
                        coordinates=coords,
                    )
                else:
                    print(f"Given '{name_prefix}' areas are not valid.")

        return {
            name: poly
            for name, poly in areafilter.basic_shapes.items()
            if name.startswith(name_prefix)
        }

    def atk_rx_detection(self):
        """
        Check if attacker can be detected by receivers.
        """

        # Compute attacker centers
        atk_centers = np.array(
            [PhysicalLayer.center(atk) for atk in self.attackers.values()]
        )  # shape (N,2)
        lat = atk_centers[:, 0]
        lon = atk_centers[:, 1]
        alt = np.zeros_like(lat)  # assuming 0 altitude; adjust if needed

        # Check all attackers against each receiver
        mask = []
        for rx in self.receivers.values():
            inside = rx.checkInside(
                lat, lon, alt
            )  # returns a boolean array of length N
            mask.append(np.any(inside))
        return np.array(mask, dtype=bool)

    def detection_masks(self):
        """
        Check if aircraft are detectable by attackers and receivers.
        Returns two boolean arrays (atk_mask, rx_mask) of shape (N,).
        """

        lat = traf.lat  # shape (N,)
        lon = traf.lon
        alt = traf.alt

        N = len(lat)
        atk_mask = np.zeros(N, dtype=bool)
        rx_mask = np.zeros(N, dtype=bool)

        # Check attackers
        for atk in self.attackers.values():
            atk_mask |= atk.checkInside(lat, lon, alt)

        # Check receivers
        for rx in self.receivers.values():
            rx_mask |= rx.checkInside(lat, lon, alt)

        return atk_mask, rx_mask

    @classmethod
    def center(cls, shape):
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

    @stack.command(name="ATK_LOC", brief="ATK_LOC [shape_name]")
    def atk_loc(self, str="") -> tuple[bool, str]:
        return True, ""

    @stack.command(name="RX_LOC", brief="RX_LOC [shape_name]")
    def rx_loc(self, str="") -> tuple[bool, str]:
        return True, ""
