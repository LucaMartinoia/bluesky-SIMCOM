import pyModeS as pms
import numpy as np
from dataclasses import dataclass, fields
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
    Inherits from TrafficArrays instead of Entity because Attacker and Receiver must own different ADSBin instances.

    Because of this, it cannot accept BlueSky decorators like Timers and Stack functions.
    """

    def __init__(self, n_rx: int = 1) -> None:
        super().__init__()

        # Counter stop and number of ADSBin
        self.max_counter = 5
        self.n_rx = n_rx

        # Create ADS-B In cache
        with self.settrafarrays():
            self.icao = []
            self.callsign = []

            self.altGNSS = []
            self.alt = []
            self.lat = []
            self.lon = []
            self.gsnorth = []
            self.gseast = []
            self.gs = []
            self.vs = []
            self.trk = []

            self.capability = []
            self.ss = []

            self.stale_counters = []

    def create(self, n: int = 1) -> None:
        """
        Initialize ADS-B In cache for newly created aircraft.
        """

        super().create(n)

        # For each aircraft, save an array of length n_rx
        for i_ac in range(-n, 0):
            self.icao[i_ac] = [""] * self.n_rx
            self.callsign[i_ac] = [""] * self.n_rx

            self.altGNSS[i_ac] = [np.nan] * self.n_rx
            self.alt[i_ac] = [np.nan] * self.n_rx
            self.lat[i_ac] = [np.nan] * self.n_rx
            self.lon[i_ac] = [np.nan] * self.n_rx
            self.gsnorth[i_ac] = [np.nan] * self.n_rx
            self.gseast[i_ac] = [np.nan] * self.n_rx
            self.gs[i_ac] = [np.nan] * self.n_rx
            self.vs[i_ac] = [np.nan] * self.n_rx
            self.trk[i_ac] = [np.nan] * self.n_rx

            self.capability[i_ac] = [0] * self.n_rx
            self.ss[i_ac] = [0] * self.n_rx

            self.stale_counters[i_ac] = [ADSBStaleCounters() for _ in range(self.n_rx)]

    def get(self, ac_idx: int, rx_idx: int = 0) -> dict:
        """
        Get ADS-B data for a specific aircraft from a specific receiver.
        """

        return {
            "icao": self.icao[ac_idx][rx_idx],
            "callsign": self.callsign[ac_idx][rx_idx],
            "altGNSS": self.altGNSS[ac_idx][rx_idx],
            "alt": self.alt[ac_idx][rx_idx],
            "lat": self.lat[ac_idx][rx_idx],
            "lon": self.lon[ac_idx][rx_idx],
            "gsnorth": self.gsnorth[ac_idx][rx_idx],
            "gseast": self.gseast[ac_idx][rx_idx],
            "gs": self.gs[ac_idx][rx_idx],
            "vs": self.vs[ac_idx][rx_idx],
            "trk": self.trk[ac_idx][rx_idx],
            "capability": self.capability[ac_idx][rx_idx],
            "ss": self.ss[ac_idx][rx_idx],
        }

    def decode_plaintext(self, msgs, i_rx: int, i_ac: int) -> None:
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
        self.update_position_altitude(pos_even, pos_odd, i_rx, i_ac)

        # Update velocity cache
        self.update_velocity(vel, i_rx, i_ac)

        # Update ID cache
        self.update_id(id, i_rx, i_ac)

    def crc_check(self, msg: str) -> bool:
        """
        Check CRC of ADS-B messages.
        """

        # Check CRC first (exluding tag)
        if not msg or pms.crc(msg[:28]) != 0:
            return False
        return True

    def set_counters(self, i_rx: int, i_ac: int, value: int = 0) -> None:
        """
        Set all stale counters for a specific aircraft/receiver pair.
        """

        counters = self.stale_counters[i_ac][i_rx]

        for f in fields(counters):
            setattr(counters, f.name, value)

    def clear_cache(self, i_rx: int, i_ac: int) -> None:
        """
        Reset the cache for given receiver and aircraft.
        """

        self.icao[i_ac][i_rx] = ""
        self.callsign[i_ac][i_rx] = ""

        self.altGNSS[i_ac][i_rx] = np.nan
        self.alt[i_ac][i_rx] = np.nan
        self.lat[i_ac][i_rx] = np.nan
        self.lon[i_ac][i_rx] = np.nan
        self.gsnorth[i_ac][i_rx] = np.nan
        self.gseast[i_ac][i_rx] = np.nan
        self.gs[i_ac][i_rx] = np.nan
        self.vs[i_ac][i_rx] = np.nan
        self.trk[i_ac][i_rx] = np.nan

        self.capability[i_ac][i_rx] = 0
        self.ss[i_ac][i_rx] = 0

    # --------------------------------------------------------------------
    #                      UPDATE VALUES
    # --------------------------------------------------------------------

    def update_position_altitude(
        self,
        msg_pos_e: str,
        msg_pos_o: str,
        i_rx: int,
        i_ac: int,
    ) -> None:
        """
        Update position and altitude cache only if decoding succeeds.
        Keeps last good values otherwise.
        """

        # Decode position
        lat_i, lon_i, icao_i = self.decode_position(msg_pos_e, msg_pos_o)

        # Update position cache
        if not np.isnan(lat_i):
            self.lat[i_ac][i_rx] = lat_i
            self.lon[i_ac][i_rx] = lon_i
            self.stale_counters[i_ac][i_rx].position = 0
        else:
            self.stale_counters[i_ac][i_rx].position += 1
            if self.stale_counters[i_ac][i_rx].position >= self.max_counter:
                self.lat[i_ac][i_rx] = np.nan
                self.lon[i_ac][i_rx] = np.nan

        # Decode altitude and surveillance status
        alt_i, ss_i, icao_tmp = self.decode_altitude_ss(msg_pos_e)
        if np.isnan(alt_i):  # type:ignore
            alt_i, ss_i, icao_tmp = self.decode_altitude_ss(msg_pos_o)

        # Update altitude cache
        if not np.isnan(alt_i):  # type:ignore
            self.alt[i_ac][i_rx] = alt_i * ft
            self.altGNSS[i_ac][i_rx] = alt_i * ft
            self.ss[i_ac][i_rx] = ss_i
            self.stale_counters[i_ac][i_rx].altitude = 0
        else:
            self.stale_counters[i_ac][i_rx].altitude += 1
            if self.stale_counters[i_ac][i_rx].altitude >= self.max_counter:
                self.alt[i_ac][i_rx] = np.nan
                self.altGNSS[i_ac][i_rx] = np.nan
                self.ss[i_ac][i_rx] = 0

        # Update ICAO if not already set
        icao_i = icao_i or icao_tmp  # keeps first valid ICAO
        self.icao[i_ac][i_rx] = icao_i if icao_i else self.icao[i_ac][i_rx]

    def update_velocity(self, msg_vel: str, i_rx: int, i_ac: int) -> None:
        """
        Update velocity cache only if decoding succeeds.
        Keeps last good values otherwise.
        """

        # Decode velocity
        speed_i, track_i, vs_i, icao_i = self.decode_velocity(msg_vel)

        # Update velocity cache
        if not np.isnan(speed_i):  # type:ignore
            self.gs[i_ac][i_rx] = speed_i * kts  # To [m/s]
            self.trk[i_ac][i_rx] = track_i
            rads = np.deg2rad(self.trk[i_ac][i_rx])
            self.gsnorth[i_ac][i_rx] = self.gs[i_ac][i_rx] * np.cos(rads)
            self.gseast[i_ac][i_rx] = self.gs[i_ac][i_rx] * np.sin(rads)
            self.vs[i_ac][i_rx] = vs_i * fpm  # To [m/s]
            self.stale_counters[i_ac][i_rx].velocity = 0
        else:
            # Or clear buffers
            self.stale_counters[i_ac][i_rx].velocity += 1
            if self.stale_counters[i_ac][i_rx].velocity >= self.max_counter:
                self.gs[i_ac][i_rx] = np.nan
                self.trk[i_ac][i_rx] = np.nan
                self.vs[i_ac][i_rx] = np.nan
                self.gsnorth[i_ac][i_rx] = np.nan
                self.gseast[i_ac][i_rx] = np.nan

        self.icao[i_ac][i_rx] = icao_i if icao_i else self.icao[i_ac][i_rx]

    def update_id(self, msg_id: str, i_rx: int, i_ac: int) -> None:
        """
        Update identification cache only if decoding succeeds.
        Keeps last good values otherwise.
        """

        # Decode callsign
        callsign_i, icao_i = self.decode_callsign(msg_id)

        # Update callsign cache
        if callsign_i:
            self.callsign[i_ac][i_rx] = callsign_i
            self.stale_counters[i_ac][i_rx].callsign = 0
        else:
            # Or clear buffers
            self.stale_counters[i_ac][i_rx].callsign += 1
            if self.stale_counters[i_ac][i_rx].callsign >= self.max_counter:
                self.callsign[i_ac][i_rx] = ""

        self.icao[i_ac][i_rx] = icao_i if icao_i else self.icao[i_ac][i_rx]

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
