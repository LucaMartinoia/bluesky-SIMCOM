import numpy as np
from bluesky import core
from bluesky.plugins.SIMCOM.adsbin import ADSBin
from dataclasses import fields
from types import SimpleNamespace

"""
This module implements receivers decoding ADS-B messages.
"""


class Receivers(core.Entity):
    """
    Class that act as ground receivers for the ADS-B messages.
    """

    def __init__(self, security, loc) -> None:
        super().__init__()

        # Global reference to security structure
        self.security = security
        self.loc = loc

        self.spoofing_map = dict()

        # Ground-receivers ADS-B In
        with self.settrafarrays():
            # Owns ADS-B In
            self.adsbin = ADSBin(self.loc.n_rx)
            # Attack detected flag
            self.detatk = []

    def create(self, n: int = 1) -> None:
        """
        Called when aircraft are created.
        """

        super().create(n)

        # For each aircraft, save an array of length n_rx
        for i_ac in range(-n, 0):
            self.detatk[i_ac] = [False] * self.loc.n_rx

    def decode(self, msgs, i_rx: int, i_ac: int) -> None:
        """
        Decode ADS-B messages for aircraft, using the
        appropriate security scheme.
        """

        scheme = self.security.scheme[i_ac] if self.security.flag else "NONE"

        atkflag = None

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
                        msg, i_ac, msg_type
                    )
                    setattr(msgs, msg_type, plaintext)

                    # If decodying fails due to cyber-attack
                    atkflag = True if not plaintext[0] else False
                    if atkflag:
                        break

                elif scheme == "NONE":
                    # Skip if no security
                    continue
                else:
                    # Unimplemented scheme
                    raise NotImplementedError(
                        f"ADS-B security scheme '{scheme}' not supported."
                    )

        # Save cyber-attack flag
        self.detatk[i_ac][i_rx] = atkflag
        # Decode plaintext ADS-B message
        self.adsbin.decode_plaintext(msgs, i_rx, i_ac)

        # If attack, reset all counters to zero so aircraft is not hidden
        if self.detatk[i_ac][i_rx]:
            self.adsbin.set_counters(i_rx, i_ac, value=0)

        # Finally, check for spoofing
        # self.check_icao_spoofing(index)

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
