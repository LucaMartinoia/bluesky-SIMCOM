from bluesky import core, traf, stack, sim
from bluesky.plugins.SIMCOM.adsbout import ADSBout, Transmission
from bluesky.plugins.SIMCOM.shared_airspace import SharedAirspace


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

            # Cryptographic nonce counter
            self.counter = []

    def create(self, n: int = 1) -> None:
        """
        Initialize newly created aircraft.
        """

        super().create(n)

        self.counter[-n:] = [0] * n

    def emit_msg(self, index: int, msg_type: str) -> Transmission:
        """
        Selected aircraft emits ADS-B messages from ADS-B Out.
        """

        # Aircraft update ADS-B Out registry from GNSS data
        self.adsbout.update_registry(traf, index)

        if self.security.flag and self.security.scheme[index] != "NONE":
            # Encode ADS-B message
            msg = self.adsbout.encode_msg(index, msg_type, crc=False)
            counter = self.counter[index]
            model = self.security.model[index]
            # Apply encryption scheme
            msg, self.counter[index] = self.security.apply_schemes(msg, counter, model)
        else:
            # Or simple ADS-B messages
            msg = self.adsbout.encode_msg(index, msg_type)

        # Update emission timer
        setattr(self.adsbout.lastemit[index], msg_type, sim.simt)

        return Transmission(msg=msg, source_loc=self.loc(index), time=0.0)

    def loc(self, index: int) -> tuple[float, float]:
        """
        Return real position of aircraft.
        """

        return (traf.lat[index], traf.lon[index])

    def last_emit(self, index: int):
        """
        Return timers of last emitted messages.
        """

        return self.adsbout.lastemit[index]

    # --------------------------------------------------------------------
    #                      STACK COMMANDS
    # --------------------------------------------------------------------

    @stack.command(name="SURVEILLANCE", brief="SURVEILLANCE acid,[status (0/1/2)]")
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

        ss = int(status)
        if 0 <= ss <= 2:
            # Otherwise apply passed status
            self.adsbout.ss[acid] = ss

            return (
                True,
                f"The surveillance status for {traf.id[acid]} is set to {status}.",
            )

        else:
            return False, f"The surveillance status {status} is not valid."
