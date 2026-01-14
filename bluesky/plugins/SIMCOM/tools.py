from bluesky import traf

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
