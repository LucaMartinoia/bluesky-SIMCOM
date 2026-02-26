import pyModeS as pms
import numpy as np
from dataclasses import dataclass, fields
from bluesky import core, sim
from bluesky.tools.aero import ft, kts, fpm
from bluesky.plugins.SIMCOM.tools import hex2bin, bin2int


"""
Module that implement ADS-B In functionalities.
"""


@dataclass
class LastReceived:
    # Data was never received
    pos: float = -np.inf
    v: float = -np.inf
    alt: float = -np.inf
    callsign: float = -np.inf


@dataclass
class StaleTimeout:
    # Timeout timers for various quantities
    pos: float = 5
    v: float = 5
    alt: float = 5
    callsign: float = 10


class ADSBin(core.TrafficArrays):
    """
    Inherits from TrafficArrays instead of Entity because Attacker and Receiver must own different ADSBin instances.

    Because of this, it cannot accept BlueSky decorators like Timers and Stack functions.
    """

    def __init__(self, n: int = 1) -> None:
        super().__init__()

        # Number of ADS-B In instances
        self.n = n

        # Timeout timer
        self.staletimeout = StaleTimeout()

        # Initialize ADS-B In cache
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

            # Timestamps of last received data
            self.lastreceived = []
            # Last valid positional message
            self.last_pos = []

    def create(self, n: int = 1) -> None:
        """
        Initialize ADS-B In cache for newly created aircraft.
        """

        super().create(n)

        # For each aircraft, save an array of length n
        for i_ac in range(-n, 0):
            self.icao[i_ac] = [""] * self.n
            self.callsign[i_ac] = [""] * self.n

            self.altGNSS[i_ac] = [np.nan] * self.n
            self.alt[i_ac] = [np.nan] * self.n
            self.lat[i_ac] = [np.nan] * self.n
            self.lon[i_ac] = [np.nan] * self.n
            self.gsnorth[i_ac] = [np.nan] * self.n
            self.gseast[i_ac] = [np.nan] * self.n
            self.gs[i_ac] = [np.nan] * self.n
            self.vs[i_ac] = [np.nan] * self.n
            self.trk[i_ac] = [np.nan] * self.n

            self.capability[i_ac] = [0] * self.n
            self.ss[i_ac] = [0] * self.n

            self.lastreceived[i_ac] = [LastReceived() for _ in range(self.n)]
            # Message, message type and timestamp
            self.last_pos[i_ac] = [("", "", -np.inf) for _ in range(self.n)]

    def get(self, ac_idx: int, rx_idx: int = 0) -> dict:
        """
        Get cached data for a specific aircraft.
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

    # --------------------------------------------------------------------
    #                      DECODE PLAINTEXT
    # --------------------------------------------------------------------

    def decode_plaintext(self, msg: str, msg_type: str, i_rx: int, i_ac: int) -> None:
        """
        Decode a plaintext ADS-B message and update the receiver cache.
        """

        if msg_type in ("even", "odd") and msg:
            # Check if we already have the other type cached
            last_msg, last_type, _ = self.last_pos[i_ac][i_rx]
            if last_type != msg_type and last_msg:
                # Determine which is even and which is odd
                if msg_type == "even":
                    pos_even = msg
                    time_even = sim.simt
                    pos_odd = last_msg
                    time_odd = self.lastreceived[i_ac][i_rx].pos
                else:
                    pos_even = last_msg
                    time_even = self.lastreceived[i_ac][i_rx].pos
                    pos_odd = msg
                    time_odd = sim.simt

                # Decode position using both messages
                ret = self.update_position(
                    pos_even, pos_odd, time_even, time_odd, i_rx, i_ac
                )
                if ret:
                    setattr(self.lastreceived[i_ac][i_rx], "pos", sim.simt)

            # Decode altitude
            ret = self.update_altitude(msg, i_rx, i_ac)
            # Update timer
            if ret:
                setattr(self.lastreceived[i_ac][i_rx], "alt", sim.simt)

            # Cache the current message
            self.last_pos[i_ac][i_rx] = (msg, msg_type, sim.simt)

        elif msg_type == "v" and msg:
            # Velocity message
            ret = self.update_velocity(msg, i_rx, i_ac)
            # Update timer
            if ret:
                setattr(self.lastreceived[i_ac][i_rx], "v", sim.simt)

        elif msg_type == "id" and msg:
            # Identification message
            ret = self.update_id(msg, i_rx, i_ac)
            # Update timer
            if ret:
                setattr(self.lastreceived[i_ac][i_rx], "callsign", sim.simt)

    def crc_check(self, msg: str) -> bool:
        """
        Check CRC of ADS-B messages.
        """

        # Check CRC first (exluding tag)
        if not msg or pms.crc(msg[:28]) != 0:
            return False
        return True

    # --------------------------------------------------------------------
    #                      CLEAR CACHES
    # --------------------------------------------------------------------

    def clear_stale_cache(self, i_ac: int, i_rx: int) -> None:
        """
        Clear caches if data is too old.
        """

        # If last received is far in the past, clear cache
        self.check_stale_position(i_ac, i_rx)
        self.check_stale_velocity(i_ac, i_rx)
        self.check_stale_altitude(i_ac, i_rx)
        self.check_stale_callsign(i_ac, i_rx)

    def check_stale_position(self, i_ac: int, i_rx: int) -> None:
        """
        Clear position cache if data is too old.
        """

        last = self.lastreceived[i_ac][i_rx].pos
        timeout = self.staletimeout.pos

        if sim.simt - last > timeout:
            self.lat[i_ac][i_rx] = np.nan
            self.lon[i_ac][i_rx] = np.nan

        last_frame = self.last_pos[i_ac][i_rx][2]
        if sim.simt - last_frame > timeout:
            self.last_pos[i_ac][i_rx] = ("", "", -np.inf)

    def check_stale_velocity(self, i_ac: int, i_rx: int) -> None:
        """
        Clear velocity cache if data is too old.
        """

        last = self.lastreceived[i_ac][i_rx].v
        timeout = self.staletimeout.v

        if sim.simt - last > timeout:
            self.gs[i_ac][i_rx] = np.nan
            self.gsnorth[i_ac][i_rx] = np.nan
            self.gseast[i_ac][i_rx] = np.nan
            self.vs[i_ac][i_rx] = np.nan
            self.trk[i_ac][i_rx] = np.nan

    def check_stale_altitude(self, i_ac: int, i_rx: int) -> None:
        """
        Clear altitude cache if data is too old.
        """

        last = self.lastreceived[i_ac][i_rx].alt
        timeout = self.staletimeout.alt

        if sim.simt - last > timeout:
            self.alt[i_ac][i_rx] = np.nan
            self.altGNSS[i_ac][i_rx] = np.nan

    def check_stale_callsign(self, i_ac: int, i_rx: int) -> None:
        """
        Clear callsign cache if data is too old.
        """

        last = self.lastreceived[i_ac][i_rx].callsign
        timeout = self.staletimeout.callsign

        if sim.simt - last > timeout:
            self.callsign[i_ac][i_rx] = ""

    def set_stale_timers(self, time: float, i_ac: int, i_rx: int) -> None:
        """
        Reset timers to default values.
        """

        last = self.lastreceived[i_ac][i_rx]

        for f in fields(last):
            setattr(last, f.name, time)

    # --------------------------------------------------------------------
    #                      UPDATE VALUES
    # --------------------------------------------------------------------

    def update_position(
        self,
        msg_pos_e: str,
        msg_pos_o: str,
        time_even: float,
        time_odd: float,
        i_rx: int,
        i_ac: int,
    ) -> bool:
        """
        Update position cache only if decoding succeeds.
        """

        # Decode position
        lat, lon, icao = self.decode_position(msg_pos_e, msg_pos_o, time_even, time_odd)

        # Update position cache
        if (
            lat is not None
            and lon is not None
            and not np.isnan(lat)
            and not np.isnan(lon)
        ):
            self.lat[i_ac][i_rx] = lat
            self.lon[i_ac][i_rx] = lon

            # Update ICAO if not already set
            self.icao[i_ac][i_rx] = self.icao[i_ac][i_rx] or icao

            return True
        return False

    def update_altitude(
        self,
        msg: str,
        i_rx: int,
        i_ac: int,
    ) -> bool:
        """
        Update altitude cache only if decoding succeeds.
        """

        # Decode altitude and surveillance status
        alt, ss, icao = self.decode_altitude_ss(msg)

        # Update altitude cache
        if alt is not None and not np.isnan(alt):  # type:ignore
            self.alt[i_ac][i_rx] = alt * ft
            self.altGNSS[i_ac][i_rx] = alt * ft
            self.ss[i_ac][i_rx] = ss

            # Update ICAO if not already set
            self.icao[i_ac][i_rx] = self.icao[i_ac][i_rx] or icao

            return True
        return False

    def update_velocity(self, msg_vel: str, i_rx: int, i_ac: int) -> bool:
        """
        Update velocity cache only if decoding succeeds.
        """

        # Decode velocity
        speed, track, vs, icao = self.decode_velocity(msg_vel)

        # Update velocity cache
        if (
            speed is not None
            and track is not None
            and vs is not None
            and not np.isnan(speed)
            and not np.isnan(track)
            and not np.isnan(vs)
        ):
            self.gs[i_ac][i_rx] = speed * kts  # To [m/s]
            self.trk[i_ac][i_rx] = track
            rads = np.deg2rad(self.trk[i_ac][i_rx])
            self.gsnorth[i_ac][i_rx] = self.gs[i_ac][i_rx] * np.cos(rads)
            self.gseast[i_ac][i_rx] = self.gs[i_ac][i_rx] * np.sin(rads)
            self.vs[i_ac][i_rx] = vs * fpm  # To [m/s]

            # Update ICAO if not already set
            self.icao[i_ac][i_rx] = self.icao[i_ac][i_rx] or icao

            return True
        return False

    def update_id(self, msg_id: str, i_rx: int, i_ac: int) -> bool:
        """
        Update identification cache only if decoding succeeds.
        """

        # Decode callsign
        callsign, icao = self.decode_callsign(msg_id)

        # Update callsign cache
        if callsign:
            self.callsign[i_ac][i_rx] = callsign

            # Update ICAO if not already set
            self.icao[i_ac][i_rx] = self.icao[i_ac][i_rx] or icao

            return True
        return False

    # --------------------------------------------------------------------
    #                      DECODE MESSAGES
    # --------------------------------------------------------------------

    def decode_position(
        self, msg_pos_e: str, msg_pos_o: str, time_even: float, time_odd: float
    ) -> tuple[float, float, str]:
        """
        Decode lat/lon from even+odd position messages.
        """

        # TODO: implement single-message position decoding
        try:
            lat, lon = pms.adsb.airborne_position(msg_pos_e, msg_pos_o, time_even, time_odd)  # type: ignore
            icao = pms.icao(msg_pos_e)
            if not icao:
                icao = pms.icao(msg_pos_o)
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
        Decode speed, track, vertical speed from velocity message.
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
        Decode callsign from identification message.
        """

        # Try decoding
        try:
            callsign = pms.adsb.callsign(msg).strip("_")
            icao = pms.icao(msg)
            return callsign, icao
        except Exception:
            return "", ""
