from bluesky import core, traf
import pyModeS as pms
import numpy as np

"""
Not sure...
"""


class Decoder(core.Entity):
    def __init__(self):
        super().__init__()

        # Create arrays for the attack arguments and cached values
        with self.settrafarrays():
            # ADS-B In data
            self.alt = np.array([], dtype=float)  # [m]
            self.lat = np.array([], dtype=float)  # latitude [deg]
            self.lon = np.array([], dtype=float)  # longitude [deg]
            self.gsnorth = np.array([], dtype=float)  # ground speed [m/s]
            self.gseast = np.array([], dtype=float)
            self.vs = np.array([], dtype=float)  # vertical speed [m/s]
            self.trk = np.array([], dtype=float)  # track angle [deg]
            self.icao = []
            self.callsign = []

    def create(self, n: int = 1) -> None:
        """
        When new aircraft are created, they are appended with a new field that stores
        the cyber-attack parameters.
        """

        super().create(n)

        # Empty cached data
        self.alt[-n:] = np.nan
        self.lat[-n:] = np.nan
        self.lon[-n:] = np.nan
        self.gsnorth[-n:] = np.nan
        self.gseast[-n:] = np.nan
        self.vs[-n:] = np.nan
        self.trk[-n:] = np.nan
        self.icao[-n:] = [""] * n
        self.callsign[-n:] = [""] * n

    def decode_plaintext(self, msgs, i):
        """
        Decode the plaintext ADS-B messages.
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

    # --------------------------------------------------------------------
    #                      UPDATE VALUES
    # --------------------------------------------------------------------

    def update_position_altitude(self, msg_pos_e, msg_pos_o, i):
        """
        Update position and altitude cache only if decoding succeeds.
        Keeps last good values otherwise.
        """
        # Decode position
        lat_i, lon_i, icao_i = self.decode_position(msg_pos_e, msg_pos_o)

        # Update posotion cache
        if lat_i is not None:
            self.lat[i] = lat_i
            self.lon[i] = lon_i
            self.stale_counters[i].position = 0
        else:
            # Or clear buffers
            self.stale_counters[i].position += 1
            if self.stale_counters[i].position >= 20:
                self.lat[i] = None
                self.lon[i] = None

        # Decode altitude and surveillance status
        alt_i, ss_i, icao_tmp = self.decode_altitude_ss(msg_pos_e)
        if alt_i is None:
            alt_i, ss_i, icao_tmp = self.decode_altitude_ss(msg_pos_o)

        # Update altitude cache
        if alt_i is not None:
            self.alt[i] = alt_i
            self.ss[i] = ss_i
            self.stale_counters[i].altitude = 0
        else:
            # Or clear buffers
            self.stale_counters[i].altitude += 1
            if self.stale_counters[i].altitude >= 20:
                self.alt[i] = None
                self.ss[i] = None

        # Update ICAO if not already set
        icao_i = icao_i or icao_tmp  # keeps first valid ICAO
        self.icao[i] = icao_i if icao_i else self.icao[i]

    def update_velocity(self, msg_vel, i):
        """
        Update velocity cache only if decoding succeeds.
        Keeps last good values otherwise.
        """
        speed_i, track_i, vs_i, icao_i = self.decode_velocity(msg_vel)

        # Update velocity cache
        if speed_i is not None:
            self.gs[i] = speed_i
            self.trk[i] = track_i
            self.vs[i] = vs_i
            self.stale_counters[i].velocity = 0
        else:
            # Or clear buffers
            self.stale_counters[i].velocity += 1
            if self.stale_counters[i].velocity >= 20:
                self.gs[i] = None
                self.trk[i] = None
                self.vs[i] = None

        self.icao[i] = icao_i if icao_i else self.icao[i]

    def update_id(self, msg_id, i):
        """
        Update identification cache only if decoding succeeds.
        Keeps last good values otherwise.
        """
        callsign_i, icao_i = self.decode_callsign(msg_id)

        if callsign_i:
            self.callsign[i] = callsign_i
            self.stale_counters[i].callsign = 0
        else:
            self.stale_counters[i].callsign += 1
            if self.stale_counters[i].callsign >= 20:
                self.callsign[i] = ""

        self.icao[i] = icao_i if icao_i else self.icao[i]

    def decode_position(self, msg_pos_e, msg_pos_o):
        """
        Decode lat/lon from even+odd position messages.
        """

        # TODO: implement single-message position decoding
        try:
            lat, lon = pms.adsb.airborne_position(msg_pos_e, msg_pos_o, 0, 1)  # type: ignore
            icao = pms.icao(msg_pos_e)
            return lat, lon, icao
        except Exception:
            return None, None, ""

    def decode_altitude_ss(self, msg):
        """
        Decode altitude and surveillance status from a single position message.
        """

        # Try decoding
        try:
            alt = pms.adsb.altitude(msg)
            msg_bin = hex2bin(msg)
            ss = bin2int(msg_bin[37:39])
            icao = pms.icao(msg)
            return alt, ss, icao
        except Exception:
            return None, None, ""

    def decode_velocity(self, msg):
        """
        Decode speed, track, vertical speed.
        """

        # Try decoding
        try:
            speed, track, vs, _ = pms.adsb.airborne_velocity(msg)  # type: ignore
            icao = pms.icao(msg)
            return speed, track, vs, icao
        except Exception:
            return None, None, None, ""

    def decode_callsign(self, msg):
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
