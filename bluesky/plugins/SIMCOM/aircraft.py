from bluesky import core, traf, stack
from bluesky.plugins.SIMCOM.adsbout import ADSBout
from bluesky.plugins.SIMCOM.shared_airspace import SharedAirspace
from bluesky.plugins.SIMCOM.physical_layer import Transmission


"""
Module for Aircraft entities in ADS-B traffic.
"""


class Aircraft(core.Entity):
    """
    Aircraft entity is a singleton that owns an ADS-B Out.
    """

    def __init__(self, security) -> None:
        super().__init__()

        # Reference to shared ADS-B security
        self.security = security

        # Aircraft systems
        with self.settrafarrays():
            # Owns ADS-B Out
            self.adsbout = ADSBout()
            # Military aircraft
            self.sharedair = SharedAirspace()

            self.last_fired = []

    def create(self, n: int = 1) -> None:
        """
        This function gets called automatically
        when new aircraft are created.
        """

        super().create(n)

        self.last_fired[-n:] = [""] * n

    def emit_msgs(self, index: int):
        """
        Selected aircraft emits ADS-B messages from ADS-B Out.
        """

        # Aircraft update ADS-B Out registry from GNSS data
        self.adsbout.update_registry(traf, index)

        if self.security.flag and self.security.scheme[index] != "NONE":
            # Apply cyber-security scheme
            msgs = self.adsbout.encode_msgs(index, crc=False)
            msgs = self.security.apply_schemes(msgs, index)
        else:
            # Or simple ADS-B messages
            msgs = self.adsbout.encode_msgs(index)

        return Transmission(msgs=msgs, source_loc=self.loc(index), time=0.0)

    def loc(self, index: int) -> tuple[float, float]:
        """
        Return real position of aircraft.
        """

        return (traf.lat[index], traf.lon[index])

    # --------------------------------------------------------------------
    #                      STACK COMMANDS
    # --------------------------------------------------------------------

    @stack.command(name="SURVEILLANCE", brief="SURVEILLANCE acid,[status (0, 1, 2)]")
    def sstatus(self, acid: "acid", status: str = "") -> tuple[bool, str]:  # type: ignore
        """
        Set the surveillance status of a given aircraft.

        If the status is not given, it returns the status of the aircraft.
        """

        # If no status, return current status
        if status == "":
            return (
                True,
                f"Aircraft {traf.id[acid]} surveillance status is {self.adsbout.ss[acid]}.",
            )

        # Otherwise apply passed status
        self.adsbout.ss[acid] = int(status)

        return True, f"The surveillance status for {traf.id[acid]} is set to {status}."
