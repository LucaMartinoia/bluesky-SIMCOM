import numpy as np
from matplotlib.path import Path
from bluesky import traf
from bluesky.network.publisher import StatePublisher
from bluesky.tools import areafilter


# --------------------------------------------------------------------
# --------------------------------------------------------------------
#                           SHAPES
# --------------------------------------------------------------------
# --------------------------------------------------------------------

# Dictionary of all basic shapes
basic_shapes = dict()
# Publisher object to manage publishing of states to clients
polypub = StatePublisher("ADSBPOLY", collect=True)


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

    return True


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
    def loc(self):
        return (self.clat, self.clon)

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

    @property
    def loc(self):
        return (self.clat, self.clon)


# --------------------------------------------------------------------
# --------------------------------------------------------------------
#                           TOOLS
# --------------------------------------------------------------------
# --------------------------------------------------------------------


def int2bin(val: int, bits: int) -> str:
    """
    Convert integer to binary string with
    left-zero padding to 'bits' length.
    """

    return f"{val:0{bits}b}"


def int2hex(val: int, digits: int) -> str:
    """
    Convert integer to hex string with
    left-zero padding to 'digits' length.
    """

    return f"{val:0{digits}X}"


def hex2bin(hexstr: str) -> str:
    """
    Convert a hexadecimal string to binary string, with zero fillings.
    """

    num_of_bits = len(hexstr) * 4
    binstr = bin(int(hexstr, 16))[2:].zfill(int(num_of_bits))
    return binstr


def hex2int(hexstr: str) -> int:
    """
    Convert a hexadecimal string to integer.
    """

    return int(hexstr, 16)


def bin2int(binstr: str) -> int:
    """
    Convert a binary string to integer.
    """

    return int(binstr, 2)


def bin2hex(binstr: str) -> str:
    """
    Convert a binary string to hexadecimal string.
    """

    return "{0:X}".format(int(binstr, 2))


def id2idx(acid):
    """
    Find index of aircraft id.
    """

    if not isinstance(acid, str):
        # id2idx is called for multiple id's
        # Fast way of finding indices of all ACID's in a given list
        tmp = dict((v, i) for i, v in enumerate(traf.id))
        # return [tmp.get(acidi, -1) for acidi in acid]
    else:
        # Catch last created id (* or # symbol)
        if acid in ("#", "*"):
            return traf.ntraf - 1

        try:
            return traf.id.index(acid.upper())
        except:
            return -1
