from pyModeS import adsb
import pyModeS as pms
from random import randint
from textwrap import wrap
from math import floor, log, cos, pi, acos

ft  = 0.3048                # m    of 1 foot


# --------------------------------------------------------------------
# --------------------------------------------------------------------
#                           TOOLS
# --------------------------------------------------------------------
# --------------------------------------------------------------------

def int2bin(val: int, bits: int) -> str:
    """Convert integer to binary string with left-zero padding to 'bits' length."""
    return f"{val:0{bits}b}"

def int2hex(val: int, digits: int) -> str:
    """Convert integer to hex string with left-zero padding to 'digits' length."""
    return f"{val:0{digits}X}"

def hex2bin(hexstr: str) -> str:
    """Convert a hexadecimal string to binary string, with zero fillings."""
    num_of_bits = len(hexstr) * 4
    binstr = bin(int(hexstr, 16))[2:].zfill(int(num_of_bits))
    return binstr

def hex2int(hexstr: str) -> int:
    """Convert a hexadecimal string to integer."""
    return int(hexstr, 16)

def bin2int(binstr: str) -> int:
    """Convert a binary string to integer."""
    return int(binstr, 2)

def bin2hex(binstr: str) -> str:
    """Convert a binary string to hexadecimal string."""
    return "{0:X}".format(int(binstr, 2))

def crc(msg: str, encode: bool = False) -> int:
    """Mode-S Cyclic Redundancy Check.

    Detect if bit error occurs in the Mode-S message. When encode option is on,
    the checksum is generated.

    Args:
        msg: 28 bytes hexadecimal message string
        encode: True to encode the date only and return the checksum
    Returns:
        int: message checksum, or partity bits (encoder)

    """
    # the CRC generator
    G = [int("11111111", 2), int("11111010", 2), int("00000100", 2), int("10000000", 2)]

    if encode:
        msg = msg[:-6] + "000000"

    msgbin = hex2bin(msg)
    msgbin_split = wrap(msgbin, 8)
    mbytes = list(map(bin2int, msgbin_split))

    for ibyte in range(len(mbytes) - 3):
        for ibit in range(8):
            mask = 0x80 >> ibit
            bits = mbytes[ibyte] & mask

            if bits > 0:
                mbytes[ibyte] = mbytes[ibyte] ^ (G[0] >> ibit)
                mbytes[ibyte + 1] = mbytes[ibyte + 1] ^ (
                    0xFF & ((G[0] << 8 - ibit) | (G[1] >> ibit))
                )
                mbytes[ibyte + 2] = mbytes[ibyte + 2] ^ (
                    0xFF & ((G[1] << 8 - ibit) | (G[2] >> ibit))
                )
                mbytes[ibyte + 3] = mbytes[ibyte + 3] ^ (
                    0xFF & ((G[2] << 8 - ibit) | (G[3] >> ibit))
                )

    result = (mbytes[-3] << 16) | (mbytes[-2] << 8) | mbytes[-1]

    return result

# --------------------------------------------------------------------
# --------------------------------------------------------------------
#                           ADS-B ENCODING
# --------------------------------------------------------------------
# --------------------------------------------------------------------


# --------------------------------------------------------------------
#                      IDENTIFICATION MESSAGES
# --------------------------------------------------------------------
def identification(ca: int, icao: str, tc: int, ec: int, callsign: str) -> str:
    """
    Encode ADS-B aircraft identification message.
    
    icao address, callsign, capability field, type code, emitter category.
    """
    # Validate ICAO
    if len(icao) != 6:
        raise ValueError("ICAO must be 6 hex digits")

    # DF and CA
    df_bin = int2bin(17, 5)        # DF = 17 for ADS-B
    ca_bin = int2bin(ca, 3)         # Capability
    
    # ICAO to binary
    icao_bin = hex2bin(icao).zfill(24)

    # Type Code and Emitter Category
    tc_bin = int2bin(tc, 5)
    ec_bin = int2bin(ec, 3)

    # Callsign: uppercase, padded/truncated to 8 characters
    callsign = callsign.upper().ljust(8)[:8]

    call_bin = ''
    for c in callsign:
        if c == ' ':
            idx = 32
        elif 'A' <= c <= 'Z':
            idx = ord(c) - ord('A') + 1
        elif '0' <= c <= '9':
            idx = ord(c)
        else:
            raise ValueError(f"Invalid character in callsign: {c}")
        call_bin += int2bin(idx, 6)

    # ME field (56 bits) = TC (5) + EC (3) + 8*6-bit callsign
    me_bin = tc_bin + ec_bin + call_bin

    # Assemble 88-bit message (without CRC)
    msg_bin = df_bin + ca_bin + icao_bin + me_bin
    msg_hex = bin2hex(msg_bin).zfill(22)  # 88 bits = 22 hex digits

    # Append 6 hex zeros (24 bits) for CRC calculation
    msg_for_crc = msg_hex + "000000"

    # Compute CRC
    crc_value = crc(msg_for_crc, encode=True)
    crc_bin = int2bin(crc_value, 24)
    crc_hex = bin2hex(crc_bin).zfill(6)

    # Full message: 112 bits, 28 hex digits
    full_msg = msg_hex + crc_hex
    return full_msg


# --------------------------------------------------------------------
#                           POSITION MESSAGES
# --------------------------------------------------------------------

def position(ca: int, icao: str, TC: int, status: int, antenna: int, alt: float, time: int, even: bool, lat: float, lon: float) -> str:
    """
    Encode ADS-B aircraft position message.
    """
    def compute_NL(lat: float) -> int:
        """
        Compute NL (longitude zone number) function.
        """
        NZ = 15 # Number of latitude zones N_z
        if abs(lat) < 10**-6: # When near the equator, NL is fixed
            return 59
        elif abs(lat) == 87: # Might be necessary to add some tolerance in the future
            return 2
        elif abs(lat) > 87: # Near the poles, NL is also fixed
            return 1
        else: # Computes the NL
            a = 1 - cos(pi / (2*NZ)) 
            b = cos(pi * lat / 180) ** 2
            nl = 2 * pi / (acos(1 - a / b))
            return int(floor(nl))

    def cpr_encode(lat: float, lon: float, even: bool):
        """
        Encode latitude and longitude using CPR.
        """
        NZ = 15 # Number of latitude zones N_z
        NL = compute_NL(lat) # Number of longitude zones NL
    
        if even:
            dLat = 360.0 / (4 * NZ) # Size of even (odd) latitude zones in degrees.
            dLon = 360.0 / max(NL, 1) # The size of even (odd) longitude zones in degrees.
        else:
            dLat = 360.0 / (4 * NZ - 1)
            dLon = 360.0 / max(NL - 1, 1)

        lat_index = floor(lat / dLat) # The index of the lat/lon zone.
        lon_index = floor(lon / dLon)
        relative_lat = lat - dLat * lat_index # Lat/lon, relative to the zone.
        relative_lon = lon - dLon * lon_index

        lat_cpr = int(relative_lat / dLat * 131072) # The relative lat, scaled to [0,1), times 2^17
        lon_cpr = int(relative_lon / dLon * 131072)

        return lat_cpr, lon_cpr

    def altitude_code_GNSS(alt: float) -> str:
        """
        Encode altitude in 12-bit. With GNSS altitude, thus it is just the altitude in meters converted to binary, which however set the maximum altitude encodable at about 4000m.
        """
        return int2bin(int(round(alt)), 12)

    def int_to_gray(n: int) -> int:
        """
        Convert an integer to Gray code.
        """
        return n ^ (n >> 1)

    def altitude_q0(alt_ft: int) -> str:
        """Encode barometric altitude above 50175 ft using Q=0."""
        alt = alt_ft + 1300
        n500 = alt // 500
        n100 = (alt % 500) // 100

        gray_n500 = int2bin(int_to_gray(n500), 8)  # 8-bit
        gray_n100 = int2bin(int_to_gray(n100), 3)  # 3-bit
        graystr = gray_n500+gray_n100

        bitstring = ['0'] * 12
        bitstring[0] = graystr[8]
        bitstring[1] = graystr[2]
        bitstring[2] = graystr[9]
        bitstring[3] = graystr[3]
        bitstring[4] = graystr[10]
        bitstring[5] = graystr[4]
        bitstring[6] = graystr[5]
        bitstring[7] = '0' # Q bit
        bitstring[8] = graystr[6]
        bitstring[9] = graystr[0]
        bitstring[10] = graystr[7]
        bitstring[11] = graystr[1]
        return ''.join(bitstring)

    def altitude_code_barometric(alt: float) -> str:
        """
        Encode barometric altitude into a 12-bit string according to ADS-B standard.
        """
        # Convert to feet and round
        alt_ft = int(round(alt / ft))
        
        # Upper limit for Q=1 encoding
        if 0 <= alt_ft < 50175:
            # Compute altitude code in 25 ft increments from -1000 ft
            N = (alt_ft + 1000) // 25
            code = int2bin(N, 11)
            full_bits = code[:6] + '1' + code[6:]  # Insert Q bit at bit position 7
        elif alt_ft >= 50175:
            full_bits = altitude_q0(alt_ft)
        else:
            raise ValueError("Altitude out of range.")
        return full_bits  # 12-bit string

    # DF = 17
    df_bin = int2bin(17, 5)
    ca_bin = int2bin(ca, 3)

    # ICAO to binary
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
    me_bin = tc_bin + ss_bin + saf_bin + alt_bin + time_bin + f_bin + y_bin_str + x_bin_str
    
    # Assemble full message (without CRC)
    msg_bin = df_bin + ca_bin + icao_bin + me_bin

    # Compute CRC
    crc_value = crc(bin2hex(msg_bin), encode=True)
    crc_bin = int2bin(crc_value, 24)

    # Final message
    full_msg = msg_bin + crc_bin

    return bin2hex(full_msg).zfill(28)



# --------------------------------------------------------------------
# --------------------------------------------------------------------
#                              TESTS FUNCTIONS
# --------------------------------------------------------------------
# --------------------------------------------------------------------

def test_identification():
    icao = f'{randint(0, 0xFFFFFF):06X}'
    callsign = 'KL204'
    capability = 5
    TC = 4
    ec = 3
    msg = identification(capability, icao, TC, ec, callsign) # identification(ca: int, icao: str, tc: int, ec: int, callsign: str) -> str:
    print("\n--- Aircraft data for identification messages --- \n"
          f"ICAO address:\t{icao}\n"
          f"callsign:\t{callsign}\n"
          f"ADS-B message:\t{msg}\n"
         )
    pms.tell(msg)

    print(f"\nICAO address match:\t{icao == pms.adsb.icao(msg)}\nCallsign match:\t\t{callsign == pms.adsb.callsign(msg).strip("_")}\n")

def test_position():
    icao = f'{randint(0, 0xFFFFFF):06X}'
    capability = 5
    TC = 9
    status = 0
    antenna = 1
    t0 = 0
    lat = 252.2657
    lon = 43.91937
    alt = int(25000 * ft) # convert from feet to meters
    msg0 = position(capability, icao, TC, status, antenna, alt, t0, True, lat, lon) # position(ca: int, icao: str, TC: int, status: int, antenna: int, alt: float, time: int, even: bool, lat: float, lon: float) -> str:
    t1 = 1
    msg1 = position(capability, icao, TC, status, antenna, alt, t1, False, lat, lon)
    print("\n--- Aircraft data for position messages --- \n"
          f"ICAO address:\t\t{icao}\n"
          f"Position LAT/LON:\t({lat}, {lon})\n"
          f"Altitude:\t\t{alt/ft:0.0f} feet\n"
          f"ADS-B even message:\t{msg0}\n"
          f"ADS-B odd message:\t{msg1}\n"
         )
    pms.tell(msg0)
    print()
    pms.tell(msg1)

    print(f"\npyModeS position: {pms.adsb.position(msg0, msg1, t0, t1)}\n\n")

if __name__=='__main__':
    test_position()

    test_identification()
