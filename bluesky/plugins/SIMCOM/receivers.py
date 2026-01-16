from bluesky import core, traf, stack
from bluesky.network.publisher import state_publisher
from bluesky.plugins.SIMCOM.adsbin import ADSBin
from dataclasses import fields

"""
This module should implement receivers decoding in SIMCOM.
"""


class Receivers(core.Entity):
    """
    Class that act as ground receivers for the ADS-B messages.
    """

    def __init__(self, security) -> None:
        """
        Initializing the receiver class.
        """

        super().__init__()

        # Global reference to security structure
        self.security = security

        # Ground-receivers ADS-B In
        with self.settrafarrays():
            # Owns ADS-B In
            self.adsbin = ADSBin()

    def create(self, n: int = 1) -> None:
        """
        When new aircraft are created, they are appended with a new field that stores
        the cyber-attack parameters.
        """

        super().create(n)

    def decode(self, msgs, index):
        """
        Decode ADS-B messages for aircraft i, using the
        appropriate security scheme.
        """
        scheme = self.security.scheme[index]

        for f in fields(msgs):
            msg_type = f.name
            msg = getattr(msgs, msg_type)

            # If message exists
            if msg[0]:
                # If CRC fails, drop message
                if not self.adsbin.crc_check(msg[0]):
                    setattr(msgs, msg_type, [""])
                    continue

                if scheme == "AES-GCM":
                    # Decrypt and authenticate before decoding
                    plaintext = self.security.decrypt_AESGCM_message(
                        msg, index, msg_type
                    )
                    setattr(msgs, msg_type, plaintext)
                    if not plaintext:
                        stack.stack(
                            f"ECHO {self.adsbin.callsign[index]} under cyber-attack."
                        )

                elif scheme == "NONE":
                    # Skip if no security
                    continue
                else:
                    # Unimplemented scheme
                    raise NotImplementedError(
                        f"ADS-B security scheme '{scheme}' not supported."
                    )

        # Decode plaintext ADS-B message
        self.adsbin.decode_plaintext(msgs, index)

    # --------------------------------------------------------------------
    #                      TODO: PUBLISHER FOR GUI
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
