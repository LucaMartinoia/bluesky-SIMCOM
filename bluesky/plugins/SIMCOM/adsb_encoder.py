import pyModeS as pms
import numpy as np
from random import randint, uniform
from math import floor, cos, pi, acos, sqrt
from bluesky.tools.aero import kts, ft, a0
from bluesky.plugins.SIMCOM.tools import hex2bin, bin2hex, int2bin

"""
This module defines functions to encode ADS-B messages given the raw data.

It can encode position, identification and airborne velocity messages.

TODO:
- vectorialize the encoding if possible
"""


def append_crc(msg_bin: str) -> str:
    """
    Take the 88 bits ADS-B message and append CRC.

    Return: the full 112 bit ADS-B message.
    """
    msg_hex = bin2hex(msg_bin).zfill(22)  # 88 bits = 22 hex digits

    # Append 6 hex zeros (24 bits) for CRC calculation
    msg_for_crc = msg_hex + "000000"

    # Compute CRC
    crc_value = pms.crc(msg_for_crc, encode=True)
    crc_hex = f"{crc_value:06X}"  # 6-digit hex directly

    # Full message: 112 bits, 28 hex digits
    full_msg = msg_hex + crc_hex
    return full_msg


# --------------------------------------------------------------------
# --------------------------------------------------------------------
#                           ADS-B ENCODING
# --------------------------------------------------------------------
# --------------------------------------------------------------------


# --------------------------------------------------------------------
#                      IDENTIFICATION MESSAGES
# --------------------------------------------------------------------


def _identification(
    ca: int, icao: str, TC: int, ec: int, callsign: str, crc: bool = True
) -> str:
    """
    Encode ADS-B identification message.

    capability field, icao address, type code, emitter category, callsign.
    """

    # Validate ICAO
    if len(icao) != 6:
        raise ValueError("ICAO must be 6 hex digits")

    # DF and CA
    df_bin = int2bin(17, 5)  # DF = 17 for ADS-B
    ca_bin = int2bin(ca, 3)  # Capability

    # ICAO to binary
    icao_bin = hex2bin(icao).zfill(24)

    # Type Code and Emitter Category
    tc_bin = int2bin(TC, 5)
    ec_bin = int2bin(ec, 3)

    # Callsign: uppercase, padded/truncated to 8 characters
    callsign = callsign.upper().ljust(8)[:8]

    call_bin = ""
    for c in callsign:
        if c == " ":
            idx = 32
        elif "A" <= c <= "Z":
            idx = ord(c) - ord("A") + 1
        elif "0" <= c <= "9":
            idx = ord(c)
        else:
            raise ValueError(f"Invalid character in callsign: {c}")
        call_bin += int2bin(idx, 6)

    # ME field (56 bits) = TC (5) + EC (3) + 8*6-bit callsign
    me_bin = tc_bin + ec_bin + call_bin

    # Assemble 88-bit message (without CRC)
    msg_bin = df_bin + ca_bin + icao_bin + me_bin

    if crc:
        # Full message: 112 bits, 28 hex digits
        full_msg = append_crc(msg_bin)
    else:
        # Full message without CRC
        full_msg = bin2hex(msg_bin).zfill(22)

    return full_msg


# --------------------------------------------------------------------
#                           POSITION MESSAGES
# --------------------------------------------------------------------


def _airborne_position(
    ca: int,
    icao: str,
    TC: int,
    status: int,
    antenna: int,
    alt: float,
    time: int,
    even: bool,
    lat: float,
    lon: float,
    crc: bool = True,
) -> str:
    # Only TC = 9 fully implemented
    """
    Encode ADS-B position message.

    capability field, icao address, type code, surveillance status,
    antenna flag/NAC, altitude, time-sync flag, parity, latitude, longitude.
    """

    def compute_NL(lat: float) -> int:
        """Compute NL (longitude zone number) function."""

        NZ = 15  # Number of latitude zones N_z
        if abs(lat) < 10**-6:  # When near the equator, NL is fixed
            return 59
        elif abs(lat) == 87:  # Might be necessary to add some tolerance
            return 2
        elif abs(lat) > 87:  # Near the poles, NL is also fixed
            return 1
        else:  # Computes the NL
            a = 1 - cos(pi / (2 * NZ))
            b = cos(pi * lat / 180) ** 2
            nl = 2 * pi / (acos(1 - a / b))
            return int(floor(nl))

    def cpr_encode(lat: float, lon: float, even: bool):
        """
        Encode latitude and longitude using CPR.
        """

        NZ = 15  # Number of latitude zones N_z
        NL = compute_NL(lat)  # Number of longitude zones NL

        if even:
            dLat = 360.0 / (4 * NZ)
            dLon = 360.0 / max(
                NL, 1
            )  # The size of even (odd) longitude zones in degrees.
        else:
            dLat = 360.0 / (4 * NZ - 1)
            dLon = 360.0 / max(NL - 1, 1)

        lat_index = floor(lat / dLat)  # The index of the lat/lon zone.
        lon_index = floor(lon / dLon)
        relative_lat = lat - dLat * lat_index  # Lat/lon, relative to the zone.
        relative_lon = lon - dLon * lon_index

        lat_cpr = int(
            relative_lat / dLat * 131072
        )  # The relative lat, scaled to [0,1), times 2^17
        lon_cpr = int(relative_lon / dLon * 131072)

        return lat_cpr, lon_cpr

    def altitude_code_GNSS(alt: float) -> str:
        """
        Encode altitude in 12-bit. With GNSS altitude, thus it is just the
        altitude in meters converted to binary, which however set the maximum
        altitude encodable at about 4000m.
        """

        return int2bin(int(round(alt)), 12)

    def int_to_gray(n: int) -> int:
        """
        Convert an integer to Gray code.
        """
        return n ^ (n >> 1)

    def altitude_q0(alt_ft: int) -> str:
        """
        Encode barometric altitude above 50175 ft using Q=0.
        """
        alt = alt_ft + 1300
        n500 = alt // 500
        n100 = (alt % 500) // 100

        gray_n500 = int2bin(int_to_gray(n500), 8)  # 8-bit
        gray_n100 = int2bin(int_to_gray(n100), 3)  # 3-bit
        graystr = gray_n500 + gray_n100

        bitstring = ["0"] * 12
        bitstring[0] = graystr[8]
        bitstring[1] = graystr[2]
        bitstring[2] = graystr[9]
        bitstring[3] = graystr[3]
        bitstring[4] = graystr[10]
        bitstring[5] = graystr[4]
        bitstring[6] = graystr[5]
        bitstring[7] = "0"  # Q bit
        bitstring[8] = graystr[6]
        bitstring[9] = graystr[0]
        bitstring[10] = graystr[7]
        bitstring[11] = graystr[1]
        return "".join(bitstring)

    def altitude_code_barometric(alt: float) -> str:
        """
        Encode barometric altitude into a 12-bit
        string according to ADS-B standard.
        """
        # Convert to feet and round
        alt_ft = int(round(alt / ft))

        # Upper limit for Q=1 encoding
        if 0 <= alt_ft < 50175:
            # Compute altitude code in 25 ft increments from -1000 ft
            alt_ft_rounded = int(round(alt_ft / 25.0) * 25)
            N = (alt_ft_rounded + 1000) // 25
            code = int2bin(N, 11)
            full_bits = code[:7] + "1" + code[7:]  # Insert Q bit
        elif alt_ft >= 50175:
            full_bits = altitude_q0(alt_ft)
        else:
            raise ValueError("Altitude out of range.")
        return full_bits  # 12-bit string

    if any(x is None or np.isnan(x) for x in [lat, lon, alt]):
        return "0" * 28  # empty ADS-B message
    # DF = 17
    df_bin = int2bin(17, 5)
    ca_bin = int2bin(ca, 3)

    # to binary
    icao_bin = hex2bin(icao).zfill(24)
    tc_bin = int2bin(TC, 5)
    ss_bin = int2bin(status, 2)
    saf_bin = int2bin(antenna, 1)
    alt_bin = altitude_code_barometric(alt)
    time_bin = int2bin(time, 1)
    f_bin = int2bin(0 if even else 1, 1)

    # CPR encode position
    y_bin, x_bin = cpr_encode(lat, lon, even)
    y_bin_str = int2bin(y_bin, 17)
    x_bin_str = int2bin(x_bin, 17)

    # Assemble ME field
    me_bin = (
        tc_bin + ss_bin + saf_bin + alt_bin + time_bin + f_bin + y_bin_str + x_bin_str
    )

    # Assemble full message (without CRC)
    msg_bin = df_bin + ca_bin + icao_bin + me_bin

    if crc:
        # Full message: 112 bits, 28 hex digits
        full_msg = append_crc(msg_bin)
    else:
        # Full message without CRC
        full_msg = bin2hex(msg_bin).zfill(22)

    return full_msg


# --------------------------------------------------------------------
#                           VELOCITY MESSAGE
# --------------------------------------------------------------------


def _airborne_velocity(
    ca: int,
    icao: str,
    IC_flag: int,
    NACv: int,
    gs_north: float,
    gs_east: float,
    vert_src: int,
    s_vert: float,
    GNSS_alt: float,
    baro_alt: float,
    crc: bool = True,
) -> str:
    # subTC 3 and 4 are not implemented
    """
    Encode ADS-B aircraft velocity message.

    capability, icao, intent change flag, NACv, ground speed north, ground speed east,
    vertical speed source, vertical speed, GNSS altitude, barometric altitude.
    """

    def encode_vertical_rate(s_vert):
        # Convert from m/s to ft/min
        vert_ftmin = s_vert / ft * 60

        # Determine sign bit: 0 for climb, 1 for descent
        vert_sign = 0 if vert_ftmin >= 0 else 1

        # Compute 9-bit vertical rate field
        vert_rate = int(abs(vert_ftmin) / 64) + 1

        return vert_sign, vert_rate

    def encode_altitude_difference(GNSS_alt, baro_alt):
        # compute difference, in meters
        dif = GNSS_alt - baro_alt

        # compute the DAlt field, in feet, 25 feet increment
        DAlt = int(round(abs(dif / ft) / 25)) + 1

        SDif = 0 if dif >= 0 else 1

        return DAlt, SDif

    def encode_velocity_gs(gs_north, gs_east):
        # check if supersonic speed
        subTC = 1 if sqrt(gs_north**2 + gs_east**2) < a0 else 2

        # compute sign of east-west and north-south ground velocities
        Dew = 0 if gs_east >= 0 else 1
        Dns = 0 if gs_north >= 0 else 1

        # compute the velocities in knots. If supersonic, multiply by 4.
        if subTC == 1:
            Vew = int(round(abs(gs_east) / kts)) + 1
            Vns = int(round(abs(gs_north) / kts)) + 1
        elif subTC == 2:
            Vew = int(round(abs(gs_east) / 4 / kts)) + 1
            Vns = int(round(abs(gs_north) / 4 / kts)) + 1

        return Dew, Vew, Dns, Vns, subTC

    vert_sign, vert_rate = encode_vertical_rate(s_vert)
    DAlt, SDif = encode_altitude_difference(GNSS_alt, baro_alt)
    Dew, Vew, Dns, Vns, subTC = encode_velocity_gs(gs_north, gs_east)
    TC = 19  # Type code is fixed for airborne velocity messages
    IFR_flag = 0  # Always zero in modern ADS-B versions

    # DF = 17
    df_bin = int2bin(17, 5)
    ca_bin = int2bin(ca, 3)

    # ICAO to binary
    icao_bin = hex2bin(icao).zfill(24)
    tc_bin = int2bin(TC, 5)
    subTC_bin = int2bin(subTC, 3)

    IC_flag_bin = int2bin(IC_flag, 1)  # Intent Change flag
    IFR_flag_bin = int2bin(IFR_flag, 1)
    NACv_bin = int2bin(NACv, 3)

    Dew_bin = int2bin(Dew, 1)
    Vew_bin = int2bin(Vew, 10)
    Dns_bin = int2bin(Dns, 1)
    Vns_bin = int2bin(Vns, 10)
    vert_src_bin = int2bin(vert_src, 1)
    vert_sign_bin = int2bin(vert_sign, 1)
    s_vert_bin = int2bin(vert_rate, 9)
    res_bin = int2bin(0, 2)  # 2 reserved bits, just set them to zero
    SDif_bin = int2bin(SDif, 1)
    DAlt_bin = int2bin(DAlt, 7)

    # Assemble ME field
    me_bin = (
        tc_bin
        + subTC_bin
        + IC_flag_bin
        + IFR_flag_bin
        + NACv_bin
        + Dew_bin
        + Vew_bin
        + Dns_bin
        + Vns_bin
        + vert_src_bin
        + vert_sign_bin
        + s_vert_bin
        + res_bin
        + SDif_bin
        + DAlt_bin
    )

    # Assemble full message (without CRC)
    msg_bin = df_bin + ca_bin + icao_bin + me_bin

    if crc:
        # Full message: 112 bits, 28 hex digits
        full_msg = append_crc(msg_bin)
    else:
        # Full message without CRC
        full_msg = bin2hex(msg_bin).zfill(22)

    return full_msg


# --------------------------------------------------------------------
# --------------------------------------------------------------------
#                              TESTS FUNCTIONS
# --------------------------------------------------------------------
# --------------------------------------------------------------------


def _test_identification():
    # Define random values
    icao = f"{randint(0, 0xFFFFFF):06X}"
    callsign = f"{randint(0, 0o7777):04o}"
    capability = 5
    TC = 4
    ec = 3
    # Encode data in ADS-B
    msg = _identification(
        capability, icao, TC, ec, callsign
    )  # identification(ca: int, icao: str, tc: int, ec: int, callsign: str)
    print(
        "\n"
        "-------------------------------------------------\n"
        "--- Aircraft data for identification messages ---\n"
        "-------------------------------------------------\n"
        f"ICAO address:\t{icao}\n"
        f"callsign:\t{callsign}\n"
        f"ADS-B message:\t{msg}\n"
    )
    # Decode messages with pyModeS
    pms.tell(msg)

    print(
        f"\nICAO address match:\t{icao == pms.adsb.icao(msg)}\n"
        f"Callsign match:\t\t{callsign == pms.adsb.callsign(msg).strip("_")}\n"
    )


def _test_position():
    # Define random values
    icao = f"{randint(0, 0xFFFFFF):06X}"
    capability = 5
    TC = 9
    status = 0
    antenna = 1
    t0 = 0
    lat = uniform(-90, 270)
    lon = uniform(-90, 90)
    alt = int(uniform(1000, 40000) * ft)  # convert from feet to meters
    # Encode data in ADS-B
    msg0 = _airborne_position(
        capability, icao, TC, status, antenna, alt, t0, True, lat, lon
    )
    t1 = 1
    msg1 = _airborne_position(
        capability, icao, TC, status, antenna, alt, t1, False, lat, lon
    )
    print(
        "\n"
        "--------------------------------------------\n"
        "---- Aircraft data for position messages ---\n"
        "--------------------------------------------\n"
        f"ICAO address:\t\t{icao}\n"
        f"Position LAT/LON:\t({lat}, {lon})\n"
        f"Altitude:\t\t{alt/ft:0.0f} feet\n"
        f"ADS-B even message:\t{msg0}\n"
        f"ADS-B odd message:\t{msg1}\n"
    )
    # Decode messages with pyModeS
    pms.tell(msg0)
    print()
    pms.tell(msg1)

    lat_S, lon_S = pms.adsb.position(msg0, msg1, t0, t1)
    alt_S = pms.adsb.altitude(msg0)

    print(
        f"\nLatitude match:\t\t{abs(lat_S - lat) < 0.01}\n"
        f"Longitude match:\t{abs(lon_S - lon) < 0.01}\n"
        f"Altitude match:\t\t{abs(alt_S - alt / ft) < 25}\n"
    )


def _test_velocity():
    # Define random values
    icao = f"{randint(0, 0xFFFFFF):06X}"
    capability = 5
    IC_flag = 0  # intent change flag
    NACv = 3  # velocity accuracy (0 bad, 4 good)
    vert_src = 1  # 0 GNSS, 1 barometric

    def random_velocity_components(max_speed=260):  # speed in m/s
        """
        Generate random north-south and east-west velocity components in knots,
        such that the total ground speed does not exceed `max_speed`.
        """
        # Sample random direction (angle) and magnitude <= max_speed
        angle = uniform(0, 2 * np.pi)
        speed = uniform(0, max_speed)

        # Compute components
        v_ew = speed * np.cos(angle)  # East-West component
        v_ns = speed * np.sin(angle)  # South-North component

        return v_ns, v_ew

    v_ns, v_ew = random_velocity_components()
    speed = np.sqrt(v_ns**2 + v_ew**2)
    track = np.degrees(np.arctan2(v_ew, v_ns)) % 360
    vert_s = uniform(-20, 20)  # vertical rate in m/s
    GNSS_alt = int(uniform(1000, 40000) * ft)  # convert from ft to m
    baro_alt = GNSS_alt + int(uniform(0, 200) * ft)  # convert from ft to m
    alt_dif = (GNSS_alt - baro_alt) / ft
    # Encoda data in ADS-B
    msg = _airborne_velocity(
        capability,
        icao,
        IC_flag,
        NACv,
        v_ns,
        v_ew,
        vert_src,
        vert_s,
        GNSS_alt,
        baro_alt,
    )
    print(
        "\n"
        "-------------------------------------------\n"
        "--- Aircraft data for velocity messages ---\n"
        "-------------------------------------------\n"
        f"ICAO address:\t\t{icao}\n"
        f"Speed:\t\t\t{speed / kts:.0f} knots\n"
        f"Track:\t\t\t{track} degrees\n"
        f"Vertical rate:\t\t{vert_s / ft * 60:.1f} feet/minute\n"
        f"GNSS-baro difference:\t{alt_dif:.0f} feet\n"
        f"ADS-B message:\t\t{msg}\n"
    )
    # Decode messages with pyModeS
    pms.tell(msg)

    speed_S, track_S, vert_S, _ = pms.adsb.velocity(msg)
    alt_dif_S = pms.adsb.altitude_diff(msg)

    print(
        f"\nSpeed match:\t\t{abs(speed / kts - speed_S) <= 4}\n"
        f"Track match:\t\t{abs(track - track_S) < 0.6}\n"
        f"Vertical rate match:\t{abs(vert_s * 60 / ft - vert_S) < 64}\n"
        f"GNSS-baro alt match:\t{abs(alt_dif_S - alt_dif) < 25}\n"
    )


# --------------------------------------------------------------------
#                      ADS-B WRAPPER FUNCTIONS
# --------------------------------------------------------------------

TYPE_CODES = dict(identification=4, position=9, velocity=19)


def identification(traf, index: int, crc: bool = True):
    """
    Encode identification ADS-B message for given aircraft index.
    """

    # Gather ADS-B data fields
    capability = traf.capability[index]
    icao = traf.icao[index]
    emitter_category = 3
    callsign = traf.callsign[index][:8].upper().ljust(8)

    # Encode and return list with hex string
    return [
        _identification(
            capability,
            icao,
            TYPE_CODES["identification"],
            emitter_category,
            callsign,
            crc,
        )
    ]


def airborne_position(traf, index: int, even: bool, crc: bool = True):
    """
    Encode position ADS-B message for given aircraft index.
    """

    # Gather ADS-B data fields
    capability = traf.capability[index]
    icao = traf.icao[index]
    ss = traf.ss[index]
    alt = traf.altbaro[index]
    lat = traf.lat[index]
    lon = traf.lon[index]

    # Encode and return list with hex string
    return [
        _airborne_position(
            capability, icao, TYPE_CODES["position"], ss, 1, alt, 0, even, lat, lon, crc
        )
    ]


def airborne_velocity(traf, index: int, crc: bool = True):
    """
    Encode velocity ADS-B message for given aircraft index.
    """

    # Gather ADS-B data fields
    capability = traf.capability[index]
    icao = traf.icao[index]
    gs_north = traf.gsnorth[index]
    gs_east = traf.gseast[index]
    ic_flag = 0
    NACv = 2
    vert_src = 1
    s_vert = traf.vs[index]
    GNSS_alt = traf.altGNSS[index]
    baro_alt = traf.altbaro[index]

    # Encode and return list with hex string
    return [
        _airborne_velocity(
            capability,
            icao,
            ic_flag,
            NACv,
            gs_north,
            gs_east,
            vert_src,
            s_vert,
            GNSS_alt,
            baro_alt,
            crc,
        )
    ]


if __name__ == "__main__":

    _test_position()
    _test_identification()
    _test_velocity()
