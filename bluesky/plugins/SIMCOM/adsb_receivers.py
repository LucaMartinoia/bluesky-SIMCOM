from bluesky import core, traf, stack  # , settings, navdb, sim, scr, tools
import pyModeS as pms
from bluesky.network.publisher import state_publisher
from bluesky.plugins.SIMCOM.tools import (
    hex2bin,
    bin2int,
)
from dataclasses import dataclass, fields

"""
This module should implement receivers decoding in SIMCOM.

TODO:
- Add network-geometry effects
- Publisher for using ICAO instead of traf.id
"""


@dataclass
class ADSBStaleCounters:
    position: int = 0
    velocity: int = 0
    altitude: int = 0
    callsign: int = 0


class ADSBreceivers(core.Entity):
    """
    Class that act as ground receivers for the ADS-B messages.
    """

    def __init__(self, security) -> None:
        """
        Initializing the receiver class.
        """

        super().__init__()

        self.security = security

        # Parameters
        self.flag = True
        self.areas = []  # List of list of tuples
        self.received = []  # List of booleans

        # Caches for the decoded values
        with self.settrafarrays():
            self.icao = []
            self.callsign = []  # identifier (string)
            self.alt = []  # [m]
            self.lat = []  # latitude [deg]
            self.lon = []  # longitude [deg]
            self.gs = []  # ground speed [m/s]
            self.vs = []  # vertical speed [m/s]
            self.trk = []  # track angle [deg]
            self.ss = []  # surveillance status
            self.stale_counters = []

    def create(self, n: int = 1) -> None:
        """
        When new aircraft are created, they are appended with a new field that stores
        the cyber-attack parameters.
        """

        super().create(n)

        # Initialize decoded values to None
        self.icao[-n:] = [""] * n
        self.callsign[-n:] = [""] * n
        self.alt[-n:] = [None] * n
        self.lat[-n:] = [None] * n
        self.lon[-n:] = [None] * n
        self.gs[-n:] = [None] * n
        self.vs[-n:] = [None] * n
        self.trk[-n:] = [None] * n
        self.ss[-n:] = [None] * n
        self.stale_counters[-n:] = [ADSBStaleCounters() for _ in range(n)]

    def decode(self, msgs, i):
        """
        Decode ADS-B messages for aircraft i, using the
        appropriate security scheme.
        """
        scheme = self.security.scheme[i]

        for f in fields(msgs):
            msg_type = f.name
            msg = getattr(msgs, msg_type)

            # If message exists
            if msg[0]:

                # If CRC fails, drop message
                if not self.crc_check(msg[0]):
                    setattr(msgs, msg_type, [""])
                    continue

                if scheme == "AES-GCM":
                    # Decrypt and authenticate before decoding
                    self.security.decrypt_AESGCM_message(msg, i, msg_type)
                    if msg[0] == "":
                        stack.stack(f"ECHO Cyberattack detected on {traf.id[i]}.")
                elif scheme == "NONE":
                    continue
                else:
                    # Unknown or unimplemented scheme
                    raise NotImplementedError(
                        f"ADS-B security scheme '{scheme}' not supported."
                    )

        self.decode_plaintext(msgs, i)

    # --------------------------------------------------------------------
    #                      PLAINTEXT DECODING
    # --------------------------------------------------------------------

    def crc_check(self, msg):
        """
        Check CRC of ADS-B messages.
        """

        # Check CRC first (exluding tag)
        if not msg or pms.crc(msg[:28]) != 0:
            return False
        return True

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

    # --------------------------------------------------------------------
    #                      TODO: PUBLISHER
    # --------------------------------------------------------------------

    # @state_publisher(topic="ADSBDATA", dt=500)
    def send_ADSB_data(self):
        """
        Broadcast ADS-B data to the GPU for displaying.

        Data are indexed by unique ICAO address, not by simulation aircraft id.

        TODO: The function is not working right now, but potentially useful for later.
        """

        data = {}

        icao, callsign, lat, lon, alt, gs, vs, trk, ss = self.aggregate_by_icao()

        # ADS-B decoded data
        data["icao"] = icao
        data["callsign"] = callsign
        data["lat"] = lat
        data["lon"] = lon
        data["alt"] = alt
        data["gs"] = gs
        data["vs"] = vs
        data["trk"] = trk
        data["ss"] = ss

        # Ground truth positions
        data["gt_lat"] = traf.lat
        data["gt_lon"] = traf.lon

        return data

    def aggregate_by_icao(self):
        """
        Aggregate decoded ADS-B data by unique ICAO address.
        For each ICAO, take the first available representative.
        """

        seen = {}
        for i, icao in enumerate(self.icao):
            if not icao:
                continue  # skip unknown ICAO
            if icao not in seen:
                seen[icao] = i

        icao_u = []
        callsign_u = []
        lat_u = []
        lon_u = []
        alt_u = []
        gs_u = []
        vs_u = []
        trk_u = []
        ss_u = []

        for icao, i in seen.items():
            icao_u.append(icao)
            callsign_u.append(self.callsign[i])
            lat_u.append(self.lat[i])
            lon_u.append(self.lon[i])
            alt_u.append(self.alt[i])
            gs_u.append(self.gs[i])
            vs_u.append(self.vs[i])
            trk_u.append(self.trk[i])
            ss_u.append(self.ss[i])

        return (
            icao_u,
            callsign_u,
            lat_u,
            lon_u,
            alt_u,
            gs_u,
            vs_u,
            trk_u,
            ss_u,
        )
