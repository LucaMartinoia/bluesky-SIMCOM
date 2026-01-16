import pyModeS as pms
import numpy as np
from dataclasses import dataclass
from bluesky import core
from bluesky.tools.aero import ft, kts, fpm
from bluesky.plugins.SIMCOM.tools import hex2bin, bin2int


"""
Module for ADS-B In implementation.
"""


@dataclass
class ADSBStaleCounters:
    position: int = 0
    velocity: int = 0
    altitude: int = 0
    callsign: int = 0


class ADSBin(core.TrafficArrays):
    """
    Inherits from TrafficArrays instead of Entity because Attacker and Receiver must own different instances.

    Because of this, it cannot accept BlueSky decorators like Timers and Stack functions.
    """

    def __init__(self) -> None:
        super().__init__()

        # Create ADS-B In cache
        with self.settrafarrays():
            self.icao = []  # hex [str]
            self.callsign = []  # [str]

            self.altGNSS = np.array([], dtype=float)  # GNSS [m]
            self.alt = np.array([], dtype=float)  # barometric [m]
            self.lat = np.array([], dtype=float)  # latitude [deg]
            self.lon = np.array([], dtype=float)  # longitude [deg]
            self.gsnorth = np.array([], dtype=float)  # ground speed [m/s]
            self.gseast = np.array([], dtype=float)  # ground speed [m/s]
            self.gs = np.array([], dtype=float)  # ground speed [m/s]
            self.vs = np.array([], dtype=float)  # vertical speed [m/s]
            self.trk = np.array([], dtype=float)  # track angle [deg]
            self.capability = []  # CA field [int]
            self.ss = []  # surveillance status [int]

            # Track data age
            self.stale_counters = []

    def create(self, n: int = 1) -> None:
        """
        Create empty cache for newly created aircraft.
        """

        super().create(n)

        # Empty cache data
        self.icao[-n:] = [""] * n
        self.callsign[-n:] = [""] * n

        self.altGNSS[-n:] = np.nan
        self.alt[-n:] = np.nan
        self.lat[-n:] = np.nan
        self.lon[-n:] = np.nan
        self.gsnorth[-n:] = np.nan
        self.gseast[-n:] = np.nan
        self.gs[-n:] = np.nan
        self.vs[-n:] = np.nan
        self.trk[-n:] = np.nan
        self.capability[-n:] = [5] * n  # 'level 2 transponder, airborne'.
        self.ss[-n:] = [0] * n  # Surveillance status

        # Stale counters
        self.stale_counters[-n:] = [ADSBStaleCounters() for _ in range(n)]

    def decode_plaintext(self, msgs, i: int) -> None:
        """
        Decode the plaintext ADS-B messages and save the data to the cache.
        """

        # Gather messages
        pos_even = msgs.position_even[0]
        pos_odd = msgs.position_odd[0]
        vel = msgs.velocity[0]
        id = msgs.identification[0]

        # Update position and altitude cache
        # TODO: split position and altitude updates
        self.update_position_altitude(pos_even, pos_odd, i)

        # Update velocity cache
        self.update_velocity(vel, i)

        # Update ID cache
        self.update_id(id, i)

    def crc_check(self, msg: str) -> bool:
        """
        Check CRC of ADS-B messages.
        """

        # Check CRC first (exluding tag)
        if not msg or pms.crc(msg[:28]) != 0:
            return False
        return True

    # --------------------------------------------------------------------
    #                      UPDATE VALUES
    # --------------------------------------------------------------------

    def update_position_altitude(self, msg_pos_e: str, msg_pos_o: str, i: int) -> None:
        """
        Update position and altitude cache only if decoding succeeds.
        Keeps last good values otherwise.
        """

        # Decode position
        lat_i, lon_i, icao_i = self.decode_position(msg_pos_e, msg_pos_o)

        # Update position cache
        if not np.isnan(lat_i):
            self.lat[i] = lat_i
            self.lon[i] = lon_i
            self.stale_counters[i].position = 0
        else:
            # Or clear buffers
            self.stale_counters[i].position += 1
            if self.stale_counters[i].position >= 20:
                self.lat[i] = np.nan
                self.lon[i] = np.nan

        # Decode altitude and surveillance status
        alt_i, ss_i, icao_tmp = self.decode_altitude_ss(msg_pos_e)
        if np.isnan(alt_i):  # type:ignore
            alt_i, ss_i, icao_tmp = self.decode_altitude_ss(msg_pos_o)

        # Update altitude cache
        if not np.isnan(alt_i):  # type:ignore
            self.alt[i] = alt_i * ft  # To [m] # type:ignore
            self.altGNSS[i] = alt_i * ft  # To [m] # type:ignore
            self.ss[i] = ss_i
            self.stale_counters[i].altitude = 0
        else:
            # Or clear buffers
            self.stale_counters[i].altitude += 1
            if self.stale_counters[i].altitude >= 20:
                self.alt[i] = np.nan
                self.altGNSS[i] = np.nan
                self.ss[i] = 0

        # Update ICAO if not already set
        icao_i = icao_i or icao_tmp  # keeps first valid ICAO
        self.icao[i] = icao_i if icao_i else self.icao[i]

    def update_velocity(self, msg_vel: str, i: int) -> None:
        """
        Update velocity cache only if decoding succeeds.
        Keeps last good values otherwise.
        """
        speed_i, track_i, vs_i, icao_i = self.decode_velocity(msg_vel)

        # Update velocity cache
        if not np.isnan(speed_i):  # type:ignore
            self.gs[i] = speed_i * kts  # To [m/s] # type:ignore
            self.trk[i] = track_i
            rads = np.deg2rad(self.trk[i])
            self.gsnorth[i] = self.gs[i] * np.cos(rads)
            self.gseast[i] = self.gs[i] * np.sin(rads)
            self.vs[i] = vs_i * fpm  # To [m/s] # type:ignore
            self.stale_counters[i].velocity = 0
        else:
            # Or clear buffers
            self.stale_counters[i].velocity += 1
            if self.stale_counters[i].velocity >= 20:
                self.gs[i] = np.nan
                self.trk[i] = np.nan
                self.vs[i] = np.nan
                self.gsnorth[i] = np.nan
                self.gseast[i] = np.nan

        self.icao[i] = icao_i if icao_i else self.icao[i]

    def update_id(self, msg_id: str, i: int) -> None:
        """
        Update identification cache only if decoding succeeds.
        Keeps last good values otherwise.
        """
        callsign_i, icao_i = self.decode_callsign(msg_id)

        # Update callsign cache
        if callsign_i:
            self.callsign[i] = callsign_i
            self.stale_counters[i].callsign = 0
        else:
            # Or clear buffers
            self.stale_counters[i].callsign += 1
            if self.stale_counters[i].callsign >= 20:
                self.callsign[i] = ""

        self.icao[i] = icao_i if icao_i else self.icao[i]

    def decode_position(
        self, msg_pos_e: str, msg_pos_o: str
    ) -> tuple[float, float, str]:
        """
        Decode lat/lon from even+odd position messages.
        """

        # TODO: implement single-message position decoding
        try:
            lat, lon = pms.adsb.airborne_position(msg_pos_e, msg_pos_o, 0, 1)  # type: ignore
            icao = pms.icao(msg_pos_e)
            return lat, lon, icao
        except Exception:
            return np.nan, np.nan, ""

    def decode_altitude_ss(self, msg: str) -> tuple[float, int, str]:
        """
        Decode altitude and surveillance status from a single position message.
        """

        # Try decoding
        try:
            alt = pms.adsb.altitude(msg)
            msg_bin = hex2bin(msg)
            ss = bin2int(msg_bin[37:39])
            icao = pms.icao(msg)
            # Validate altitude
            if alt < 0 or alt > 999999:  # type:ignore
                alt = np.nan
            return alt, ss, icao  # type: ignore
        except Exception:
            return np.nan, 0, ""

    def decode_velocity(self, msg: str) -> tuple[float, float, float, str]:
        """
        Decode speed, track, vertical speed.
        """

        # Try decoding
        try:
            speed, track, vs, _ = pms.adsb.airborne_velocity(msg)  # type: ignore
            icao = pms.icao(msg)
            return speed, track, vs, icao  # type: ignore
        except Exception:
            return np.nan, np.nan, np.nan, ""

    def decode_callsign(self, msg: str) -> tuple[str, str]:
        """
        Decode callsign.
        """

        # Try decoding
        try:
            callsign = pms.adsb.callsign(msg).strip("_")
            icao = pms.icao(msg)
            return callsign, icao
        except Exception:
            return "", ""
