from bluesky import core, sim
from bluesky.plugins.SIMCOM.adsbin import ADSBin


"""
This module implements receivers decoding ADS-B messages.
"""


class Receivers(core.Entity):
    """
    Class that act as ground receivers for the ADS-B messages.
    """

    def __init__(self, security) -> None:
        super().__init__()

        # Global reference to security
        self.security = security
        # Spatial references
        self.area = []
        self._n = 1

        self.spoofing_map = dict()
        self.atktimeout = 20  # [s]

        # Ground-receivers ADS-B In
        with self.settrafarrays():
            # Owns ADS-B In
            self.adsbin = ADSBin(self._n)
            # Attack detected flag and timestamp
            self.lastatkdet = []
            self.atkflag = []

            # Cryptographic nonce cache
            self.nonces = []

    @property
    def n(self):
        """
        Getter method for the number of receivers.
        """

        return self._n

    @n.setter
    def n(self, n: int) -> None:
        """
        Setter method to set number of receivers.
        """

        self._n = n
        self.adsbin.n = n

    def create(self, n: int = 1) -> None:
        """
        Initialize parameters for newly created aircraft.
        """

        super().create(n)

        # For each aircraft, save an array of length n_rx
        for i_ac in range(-n, 0):
            # Last atk detection in the far past
            self.lastatkdet[i_ac] = [-1e9] * self.n
            self.atkflag[i_ac] = [False] * self.n

            self.nonces[i_ac] = [b""] * self.n

    def decode(self, msg: list, msg_type: str, i_rx: int, i_ac: int) -> None:
        """
        Model the receiver-side processing pipeline: CRC validation, optional
        decryption/authentication, and plaintext ADS-B decoding.

        Security is applied per-aircraft according to the configured scheme.
        If authentication fails, the event is treated as a cyber-attack detection.
        Otherwise, the message is passed to the plaintext decoder and the
        attack flag is cleared after a timeout.
        """

        flag = self.security.flag
        scheme = self.security.scheme[i_ac] if flag else "NONE"

        # If message is empty, nothing to decode
        if not msg[0]:
            return

        # CRC check
        if not self.adsbin.crc_check(msg[0]):
            return

        if scheme == "AES-GCM":
            # Decrypt and authenticate
            cached_nonce = self.nonces[i_ac][i_rx]
            model = self.security.model[i_ac]
            msg, self.nonces[i_ac][i_rx] = self.security.decrypt_AESGCM(
                msg, cached_nonce, model
            )

        elif scheme == "NONE":
            # No security, turn off detection flag
            self.atkflag[i_ac][i_rx] = False

        else:
            print(f"ADS-B security scheme '{scheme}' not supported.")

        # If authentication failed is due to cyber-attack
        if not msg[0] and flag:
            self.lastatkdet[i_ac][i_rx] = sim.simt
            self.atkflag[i_ac][i_rx] = True
            self.adsbin.set_stale_timers(time=sim.simt, i_ac=i_ac, i_rx=i_rx)
        # Decode plaintext ADS-B message
        else:
            self.adsbin.decode_plaintext(msg[0], msg_type, i_rx, i_ac)

            # If last attack detection is far in the past, remove flag
            if self.lastatkdet[i_ac][i_rx] + self.atktimeout <= sim.simt:
                self.atkflag[i_ac][i_rx] = False

    def check_icao_spoofing(self, index: int) -> None:
        """
        Check whether the ICAO of aircraft `index` has already
        appeared. If so, mark all involved aircraft as spoofed.
        """

        # Gather current ICAO
        icao = self.adsbin.icao[index]
        if not icao:
            return  # unknown ICAO, ignore

        if icao not in self.spoofing_map:
            # First time we see this ICAO
            self.spoofing_map[icao] = [index]
        else:
            # ICAO already seen: spoofing detected
            self.spoofing_map[icao].append(index)

    def clear_stale_cache(self, i_ac: int, i_rx: int) -> None:
        """
        Helper function to clear ADS-B In stale caches.
        """

        self.adsbin.clear_stale_cache(i_ac, i_rx)
