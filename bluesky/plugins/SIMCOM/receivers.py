from bluesky import core, sim
from bluesky.plugins.SIMCOM.adsbin import ADSBin


"""
This module implements receivers decoding ADS-B messages.
"""


class Receivers(core.Entity):
    """
    Class that act as ground receivers for the ADS-B messages.
    """

    def __init__(self, security, loc) -> None:
        super().__init__()

        # Global reference to security and locations
        self.security = security
        self.loc = loc

        self.spoofing_map = dict()
        self.atktimeout = 20  # [s]

        # Ground-receivers ADS-B In
        with self.settrafarrays():
            # Owns ADS-B In
            self.adsbin = ADSBin(self.loc.n_rx)
            # Attack detected flag and timestamp
            self.lastatkdet = []
            self.atkflag = []

            # Cryptographic nonce cache
            self.nonces = []

    def create(self, n: int = 1) -> None:
        """
        Called when aircraft are created.
        """

        super().create(n)

        # For each aircraft, save an array of length n_rx
        for i_ac in range(-n, 0):
            # Last atk detection in the far past
            self.lastatkdet[i_ac] = [-1e-9] * self.loc.n_rx
            self.atkflag[i_ac] = [False] * self.loc.n_rx

            self.nonces[-n:] = [b""] * n

    def decode(self, msg: list, msg_type: str, i_rx: int, i_ac: int) -> None:
        """
        Decode ADS-B messages for aircraft, using the
        appropriate security scheme.
        """

        scheme = self.security.scheme[i_ac] if self.security.flag else "NONE"

        # If message is empty, nothing to decode
        if not msg[0]:
            return

        # CRC check
        if not self.adsbin.crc_check(msg[0]):
            return

        if scheme == "AES-GCM":
            # Decrypt and authenticate
            cached_nonce = self.nonces[i_ac]
            model = self.security.model[i_ac]
            msg, self.nonces[i_ac] = self.security.decrypt_AESGCM(
                msg, cached_nonce, model
            )

        elif scheme == "NONE":
            # No security
            pass

        else:
            print(f"ADS-B security scheme '{scheme}' not supported.")

        # If authentication failed is due to cyber-attack
        if not msg[0]:
            self.lastatkdet[i_ac][i_rx] = sim.simt
            self.atkflag[i_ac][i_rx] = True
            self.adsbin.set_stale_timers(time=sim.simt, i_ac=i_ac, i_rx=i_rx)
        # Decode plaintext ADS-B message
        else:
            self.adsbin.decode_plaintext(msg[0], msg_type, i_rx, i_ac)

            # If last atk is far in the past, remove flag
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
