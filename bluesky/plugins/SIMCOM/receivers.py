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

    def __init__(self, security) -> None:
        super().__init__()

        # Global reference to security structure
        self.security = security

        self.spoofing_map = dict()

        # Ground-receivers ADS-B In
        with self.settrafarrays():
            # Owns ADS-B In
            self.adsbin = ADSBin()
            # Attack detected flag
            self.detatk: list[bool] = []

    def create(self, n: int = 1) -> None:
        """
        Called when aircraft are created.
        """
        super().create(n)

        self.detatk[-n:] = [False] * n

    def decode(self, msgs, index: int) -> None:
        """
        Decode ADS-B messages for aircraft, using the
        appropriate security scheme.
        """
        scheme = self.security.scheme[index] if self.security.flag else "NONE"

        atkflag = SimpleNamespace()

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

                    # If decodying fails due to cyber-attack
                    flag = True if not plaintext[0] else False
                    setattr(atkflag, msg_type, flag)

                elif scheme == "NONE":
                    # Skip if no security
                    continue
                else:
                    # Unimplemented scheme
                    raise NotImplementedError(
                        f"ADS-B security scheme '{scheme}' not supported."
                    )

        # Save cyber-attack flag
        self.detatk[index] = any(vars(atkflag).values())
        # Decode plaintext ADS-B message
        self.adsbin.decode_plaintext(msgs, index)

        # If attack, reset all counters to zero so aircraft is not hidden
        if self.detatk[index]:
            self.reset_counters(index)

        # Finally, check for spoofing
        self.check_icao_spoofing(index)

    def reset_counters(self, index) -> None:
        """
        Reset all counters to zero.
        """

        counters = self.adsbin.stale_counters[index]

        for f in fields(counters):
            setattr(counters, f.name, 0)

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
