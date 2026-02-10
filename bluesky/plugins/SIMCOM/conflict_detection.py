import numpy as np
from bluesky import stack, traf, settings, core
from bluesky.tools import geo
from bluesky.tools.aero import nm, ft

"""
Ground perspective of state-based conflict detection based on ADS-B data.
"""

# Eventually, we can create a new set of ASAS settings.


class ConflictDetection(core.Entity, replaceable=True):
    """
    Conflict Detection implementations.
    """

    def __init__(self) -> None:
        super().__init__()
        ## Default values
        # [m] Horizontal separation minimum for detection
        self.rpz_def = settings.asas_pzr * nm
        self.global_rpz = True
        # [m] Vertical separation minimum for detection
        self.hpz_def = settings.asas_pzh * ft
        self.global_hpz = True
        # [s] lookahead time
        self.dtlookahead_def = settings.asas_dtlookahead
        self.global_dtlook = True
        self.dtnolook_def = 0.0
        self.global_dtnolook = True

        # Conflicts and LoS detected in the current timestep (used for resolving)
        self.confpairs = list()
        self.lospairs = list()
        self.qdr = np.array([])
        self.dist = np.array([])
        self.dcpa = np.array([])
        self.tcpa = np.array([])
        self.tLOS = np.array([])
        # Unique conflicts and LoS in the current timestep (a, b) = (b, a)
        self.confpairs_unique = set()
        self.lospairs_unique = set()

        # All conflicts and LoS since simt=0
        self.confpairs_all = list()
        self.lospairs_all = list()

        # Turn on/off conflict detection
        self.flag = True

        # Per-aircraft conflict data
        with self.settrafarrays():
            self.inconf = np.array([], dtype=bool)  # In-conflict flag
            self.tcpamax = np.array([])  # Maximum time to CPA for aircraft in conflict
            # [m] Horizontal separation minimum for detection
            self.rpz = np.array([])
            # [m] Vertical separation minimum for detection
            self.hpz = np.array([])
            # [s] lookahead time
            self.dtlookahead = np.array([])
            self.dtnolook = np.array([])

    def clearconfdb(self) -> None:
        """
        Clear conflict database.
        """
        self.confpairs_unique.clear()
        self.lospairs_unique.clear()
        self.confpairs.clear()
        self.lospairs.clear()
        self.qdr = np.array([])
        self.dist = np.array([])
        self.dcpa = np.array([])
        self.tcpa = np.array([])
        self.tLOS = np.array([])
        self.inconf = np.zeros(len(traf.id))
        self.tcpamax = np.zeros(len(traf.id))

    def create(self, n: int) -> None:
        """
        Default values for new aircraft.
        """
        super().create(n)
        # Initialise values of own states
        self.rpz[-n:] = self.rpz_def
        self.hpz[-n:] = self.hpz_def
        self.dtlookahead[-n:] = self.dtlookahead_def
        self.dtnolook[-n:] = self.dtnolook_def

    def reset(self) -> None:
        """
        Reset all values.
        """
        super().reset()
        self.clearconfdb()
        self.confpairs_all.clear()
        self.lospairs_all.clear()
        self.rpz_def = settings.asas_pzr * nm
        self.hpz_def = settings.asas_pzh * ft
        self.dtlookahead_def = settings.asas_dtlookahead
        self.dtnolook_def = 0.0
        self.global_rpz = self.global_hpz = True
        self.global_dtlook = self.global_dtnolook = True

    def update(self, ownship, intruder) -> None:
        """
        Perform an update step of the Conflict Detection implementation.
        """

        # If there are no aircraft or detection is off, pass
        if self.flag and len(ownship.callsign) != 0:
            (
                self.confpairs,
                self.lospairs,
                self.inconf,
                self.tcpamax,
                self.qdr,
                self.dist,
                self.dcpa,
                self.tcpa,
                self.tLOS,
            ) = self.detect(ownship, intruder, self.rpz, self.hpz, self.dtlookahead)
        else:
            (
                self.confpairs,
                self.lospairs,
                self.inconf,
                self.tcpamax,
                self.qdr,
                self.dist,
                self.dcpa,
                self.tcpa,
                self.tLOS,
            ) = self.pass_detect()

        # confpairs has conflicts observed from both sides (a, b) and (b, a)
        # confpairs_unique keeps only one of these
        confpairs_unique = {frozenset(pair) for pair in self.confpairs}
        lospairs_unique = {frozenset(pair) for pair in self.lospairs}

        self.confpairs_all.extend(confpairs_unique - self.confpairs_unique)
        self.lospairs_all.extend(lospairs_unique - self.lospairs_unique)

        # Update confpairs_unique and lospairs_unique
        self.confpairs_unique = confpairs_unique
        self.lospairs_unique = lospairs_unique

    def pass_detect(self):
        """
        Pass detect.
        """
        confpairs = []
        lospairs = []
        inconf = np.zeros(len(traf.id))
        tcpamax = np.zeros(len(traf.id))
        qdr = np.array([])
        dist = np.array([])
        dcpa = np.array([])
        tcpa = np.array([])
        tLOS = np.array([])

        return confpairs, lospairs, inconf, tcpamax, qdr, dist, dcpa, tcpa, tLOS

    # --------------------------------------------------------------------
    #                      DETECTION ALGORITHMS
    # --------------------------------------------------------------------

    def detect(self, ownship, intruder, rpz, hpz, dtlookahead):
        """
        Conflict detection between ownship and intruder.
        """
        # Identity matrix of order ntraf: avoid ownship-ownship detected conflicts
        n = traf.ntraf
        I = np.eye(n)

        # Horizontal conflict ------------------------------------------------------

        # qdrlst is for [i,j] qdr from i to j, from perception of ADSB and own coordinates
        qdr, dist = geo.kwikqdrdist_matrix(
            np.asmatrix(ownship.lat),
            np.asmatrix(ownship.lon),
            np.asmatrix(intruder.lat),
            np.asmatrix(intruder.lon),
        )

        # Convert back to array to allow element-wise array multiplications later on
        # Convert to meters and add large value to own/own pairs
        qdr = np.asarray(qdr)
        dist = np.asarray(dist) * nm + 1e9 * I

        # Calculate horizontal closest point of approach (CPA)
        qdrrad = np.radians(qdr)
        dx = dist * np.sin(qdrrad)  # is pos j rel to i
        dy = dist * np.cos(qdrrad)  # is pos j rel to i

        # Ownship track angle and speed
        owntrkrad = np.radians(ownship.trk)
        ownu = ownship.gs * np.sin(owntrkrad).reshape((1, n))  # m/s
        ownv = ownship.gs * np.cos(owntrkrad).reshape((1, n))  # m/s

        # Intruder track angle and speed
        inttrkrad = np.radians(intruder.trk)
        intu = intruder.gs * np.sin(inttrkrad).reshape((1, n))  # m/s
        intv = intruder.gs * np.cos(inttrkrad).reshape((1, n))  # m/s

        du = ownu - intu.T  # Speed du[i,j] is perceived eastern speed of i to j
        dv = ownv - intv.T  # Speed dv[i,j] is perceived northern speed of i to j

        dv2 = du * du + dv * dv
        dv2 = np.where(np.abs(dv2) < 1e-6, 1e-6, dv2)  # limit lower absolute value
        vrel = np.sqrt(dv2)

        tcpa = -(du * dx + dv * dy) / dv2 + 1e9 * I

        # Calculate distance^2 at CPA (minimum distance^2)
        dcpa2 = np.abs(dist * dist - tcpa * tcpa * dv2)

        # Check for horizontal conflict
        # RPZ can differ per aircraft, get the largest value per aircraft pair
        rpz = np.asarray(np.maximum(np.asmatrix(rpz), np.asmatrix(rpz).transpose()))
        R2 = rpz * rpz
        swhorconf = dcpa2 < R2  # conflict or not

        # Calculate times of entering and leaving horizontal conflict
        dxinhor = np.sqrt(
            np.maximum(0.0, R2 - dcpa2)
        )  # half the distance travelled inzide zone
        dtinhor = dxinhor / vrel

        tinhor = np.where(swhorconf, tcpa - dtinhor, 1e8)  # Set very large if no conf
        touthor = np.where(swhorconf, tcpa + dtinhor, -1e8)  # set very large if no conf

        # Vertical conflict --------------------------------------------------------

        # Vertical crossing of disk (-dh,+dh)
        dalt = ownship.alt.reshape((1, n)) - intruder.alt.reshape((1, n)).T + 1e9 * I

        dvs = ownship.vs.reshape(1, n) - intruder.vs.reshape(1, n).T
        dvs = np.where(np.abs(dvs) < 1e-6, 1e-6, dvs)  # prevent division by zero

        # Check for passing through each others zone
        # hPZ can differ per aircraft, get the largest value per aircraft pair
        hpz = np.asarray(np.maximum(np.asmatrix(hpz), np.asmatrix(hpz).transpose()))
        tcrosshi = (dalt + hpz) / -dvs
        tcrosslo = (dalt - hpz) / -dvs
        tinver = np.minimum(tcrosshi, tcrosslo)
        toutver = np.maximum(tcrosshi, tcrosslo)

        # Combine vertical and horizontal conflict----------------------------------
        tinconf = np.maximum(tinver, tinhor)
        toutconf = np.minimum(toutver, touthor)

        swconfl = np.array(
            swhorconf
            * (tinconf <= toutconf)
            * (toutconf > 0.0)
            * np.asarray(tinconf < np.asmatrix(dtlookahead).T)
            * (1.0 - I),
            dtype=bool,
        )

        # --------------------------------------------------------------------------
        # Update conflict lists
        # --------------------------------------------------------------------------
        # Ownship conflict flag and max tCPA
        inconf = np.any(swconfl, 1)
        tcpamax = np.max(tcpa * swconfl, 1)

        # Select conflicting pairs: each a/c gets their own record
        confpairs = [(traf.id[i], traf.id[j]) for i, j in zip(*np.where(swconfl))]
        swlos = (dist < rpz) * (np.abs(dalt) < hpz)
        lospairs = [(traf.id[i], traf.id[j]) for i, j in zip(*np.where(swlos))]

        return (
            confpairs,
            lospairs,
            inconf,
            tcpamax,
            qdr[swconfl],
            dist[swconfl],
            np.sqrt(dcpa2[swconfl]),
            tcpa[swconfl],
            tinconf[swconfl],
        )

    # --------------------------------------------------------------------
    #                      STACK FUNCTIONS
    # --------------------------------------------------------------------

    @stack.command(name="ADSBZONE", brief="ADSBZONE radius,acid")
    def setrpz(self, radius: float = -1.0, *acidx: "acid") -> tuple[bool, str]:  # type: ignore
        """
        Set the vertical/horizontal separation distance (i.e., the radius of the
        protected zone) in nautical miles.

        Arguments:
        - radius: The protected zone radius in nautical miles
        - acidx: Aircraft id(s) or group. When this argument is not provided the default PZ radius is changed.
          Otherwise the PZ radius for the passed aircraft is changed.
        """

        if radius < 0.0:
            return (
                True,
                f"ADSBZONE [radius(nm), acid]\nCurrent default PZ radius: {self.rpz_def / nm:.2f} NM",
            )
        if len(acidx) > 0:
            if isinstance(acidx[0], np.ndarray):
                acidx = acidx[0]  # type:ignore
            self.rpz[acidx] = radius * nm
            self.global_rpz = False
            return True, f"Setting PZ radius to {radius} NM for {len(acidx)} aircraft"
        oldradius = self.rpz_def
        self.rpz_def = radius * nm
        if self.global_rpz:
            self.rpz[:] = self.rpz_def

        return True, f"Setting default PZ radius to {radius} NM"

    @stack.command(name="ADSBCD", brief="ADSBCD [flag]")
    def selection(self, flag: str = "") -> tuple[bool, str]:
        """
        Turn ON/OFF the Conflict Detection methods for ADS-B data.
        """

        if flag == "":
            # No argument: flip current state
            self.flag = not self.flag
        else:
            f = flag.lower()
            if f == "true":
                self.flag = True
            elif f == "false":
                self.flag = False
            else:
                return False, "Flag must be True or False."

        state = "ON" if self.flag else "OFF"
        return True, f"Conflict Detection for ADS-B is {state}."

    @stack.command(name="ADSBDTLOOK", brief="ADSBDTLOOK [time],[acid]")
    def setdtlook(self, time: "time" = -1.0, *acidx: "acid") -> tuple[bool, str]:  # type: ignore
        """
        Set the lookahead time (in [hh:mm:]sec) for conflict detection.
        """

        if time < 0.0:
            return (
                True,
                f"DTLOOK[time]\nCurrent value: {self.dtlookahead_def: .1f} sec.",
            )
        if len(acidx) > 0:
            if isinstance(acidx[0], np.ndarray):
                acidx = acidx[0]
            self.dtlookahead[acidx] = time
            self.global_dtlook = False
            return (
                True,
                f"Setting CD lookahead to {time} sec for {len(acidx)} aircraft.",
            )
        self.dtlookahead_def = time
        if self.global_dtlook:
            self.dtlookahead[:] = time
        return True, f"Setting default CD lookahead to {time} sec."
