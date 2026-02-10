from bluesky import core, stack, traf, settings


"""
SIMCOM module that implements military aircraft in shared airspace.

OpenAP does not have military aircraft performance. The closest ones (used for certain operations) could be
B737 as AWACS platform or GLF6 for command operations/VIP platform.

We model military AC as operating in OAT, thus are not affected by Conflict Detection/Resolution:
they never deviate from their path, but other AC are forced to move away from them.
"""


class SharedAirspace(core.Entity):
    """
    Class that implements military aircraft in shared airspace.
    """

    def __init__(self) -> None:
        super().__init__()

        # Create arrays for the attack arguments and attack type
        with self.settrafarrays():
            self.role = []

    def create(self, n: int = 1) -> None:
        """
        When new aircraft are created, they are appended with a new field that stores
        their role in shared airspace.
        """

        super().create(n)

        self.role[-n:] = ["CIVIL"] * n

    # --------------------------------------------------------------------
    #                      STACK FUNCTIONS
    # --------------------------------------------------------------------

    @stack.command(name="MILCRE", brief="MILCRE acid,lat,lon,hdg,alt,spd")
    def military_cre(
        self, acid: str, lat: float, lon: float, hdg: float, alt: str, spd: float
    ) -> bool:
        """
        Creates a Military aircraft.
        """

        # Create GLF6 aircraft
        stack.stack(f"CRE {acid}, glf6, {lat}, {lon}, {hdg}, {alt}, {spd}")

        # Set role to MILITARY
        self.role[-1] = "MILITARY"

        # Disable Conflict Detection
        stack.stack(f"ADSBDTLOOK 0 {acid}")

        return True

    @stack.command(name="ROLE", brief="ROLE acid,[role (CIVIL/MILITARY)]")
    def set_role(self, acid: "acid", role: str = "") -> tuple[bool, str]:  # type: ignore
        """
        Assign the role to a given aircraft.

        If no role is provided, it returns the current role.
        """

        # If a GHOST, return
        if self.role[acid] == "":
            return False, f"GHOST aircraft do not have roles."
        # If it is not a ghost, getter and setter
        elif role == "":
            return True, f"{traf.id[acid]} is currently a {self.role[acid]} aircraft."
        elif role == "CIVIL":
            self.role[acid] = role
            stack.stack(f"ADSBDTLOOK {settings.asas_dtlookahead} {traf.id[acid]}")
            return True, f"{traf.id[acid]} role set to {role}."
        elif role == "MILITARY":
            self.role[acid] = role
            stack.stack(f"ADSBDTLOOK 0 {traf.id[acid]}")
            return True, f"{traf.id[acid]} role set to {role}."

        # Fallback case
        else:
            return False, f"{role} is not a valid role. Must be CIVIL or MILITARY."
