import numpy as np
from bluesky import stack, traf, settings, core
from bluesky.tools import geo
from bluesky.tools.aero import nm, ft, kts
import pyModeS as pms

"""State-based conflict detection based on ADS-B data."""

"""
class ConflictDetection(core.Entity, replaceable=True):
    """Conflict Detection implementations."""

    def __init__(self):
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
        self.cd_flag = True

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

    def clearconfdb(self):
        """Clear conflict database."""
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

    def create(self, n):
        super().create(n)
        # Initialise values of own states
        self.rpz[-n:] = self.rpz_def
        self.hpz[-n:] = self.hpz_def
        self.dtlookahead[-n:] = self.dtlookahead_def
        self.dtnolook[-n:] = self.dtnolook_def

    def reset(self):
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

    def update(self, ownship, intruder):
        """Perform an update step of the Conflict Detection implementation."""

        # If there are no aircraft or detection is off, pass
        if self.cd_flag and len(ownship.callsign) != 0:
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
            ) = self.detect_statebased(
                ownship, intruder, self.rpz, self.hpz, self.dtlookahead
            )
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
            ) = self.detect(ownship, intruder, self.rpz, self.hpz, self.dtlookahead)

        # confpairs has conflicts observed from both sides (a, b) and (b, a)
        # confpairs_unique keeps only one of these
        confpairs_unique = {frozenset(pair) for pair in self.confpairs}
        lospairs_unique = {frozenset(pair) for pair in self.lospairs}

        self.confpairs_all.extend(confpairs_unique - self.confpairs_unique)
        self.lospairs_all.extend(lospairs_unique - self.lospairs_unique)

        # Update confpairs_unique and lospairs_unique
        self.confpairs_unique = confpairs_unique
        self.lospairs_unique = lospairs_unique

    def detect(self, ownship, intruder, rpz, hpz, dtlookahead):
        """Detect any conflicts between ownship and intruder.
        This function should be reimplemented in a subclass for actual
        detection of conflicts. See for instance
        bluesky.traffic.asas.statebased.
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

    def detect_statebased(self, ownship, intruder, rpz, hpz, dtlookahead):
        """Conflict detection between ownship (traf) and intruder (traf/adsb)."""

        # Identity matrix of order ntraf: avoid ownship-ownship detected conflicts
        n = len(traf.id)
        I = np.eye(n)

        lat, lon, alt, gs, trk, vs = [], [], [], [], [], []
        # Decode using pyModeS
        for i in range(n):
            try:
                lat_i, lon_i = pms.adsb.airborne_position(
                    str(ownship.msg_pos_e[i]),
                    str(ownship.msg_pos_o[i]),
                    0,
                    1,
                )
                alt_i = pms.adsb.altitude(str(ownship.msg_pos_e[i]))
                gs_i, trk_i, vs_i, _ = pms.adsb.velocity(str(ownship.msg_v[i]))

            except Exception:
                lat_i, lon_i, alt_i, gs_i, trk_i, vs_i = (
                    np.nan,
                    np.nan,
                    np.nan,
                    np.nan,
                    np.nan,
                    np.nan,
                )

            lat.append(lat_i)
            lon.append(lon_i)
            alt.append(alt_i * ft)
            gs.append(gs_i * kts)
            trk.append(trk_i)
            vs.append(vs_i)

        # Convert lists to NumPy arrays for efficient computation
        lat = np.array(lat, dtype=float)
        lon = np.array(lon, dtype=float)
        alt = np.array(alt, dtype=float)
        gs = np.array(gs, dtype=float)
        trk = np.array(trk, dtype=float)
        vs = np.array(vs, dtype=float)

        # Horizontal conflict ------------------------------------------------------

        # qdrlst is for [i,j] qdr from i to j, from perception of ADSB and own coordinates
        qdr, dist = geo.kwikqdrdist_matrix(
            np.asmatrix(lat),
            np.asmatrix(lon),
            np.asmatrix(lat),
            np.asmatrix(lon),
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
        owntrkrad = np.radians(trk)
        ownu = gs * np.sin(owntrkrad).reshape((1, n))  # m/s
        ownv = gs * np.cos(owntrkrad).reshape((1, n))  # m/s

        # Intruder track angle and speed
        inttrkrad = np.radians(trk)
        intu = gs * np.sin(inttrkrad).reshape((1, n))  # m/s
        intv = gs * np.cos(inttrkrad).reshape((1, n))  # m/s

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
        dalt = alt.reshape((1, n)) - alt.reshape((1, n)).T + 1e9 * I

        dvs = vs.reshape(1, n) - vs.reshape(1, n).T
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

    @stack.command(name="ADSBZONE")
    def setrpz(self, radius: float = -1.0, *acidx: "acid"):
        """Set the vertical/horizontal separation distance (i.e., the radius of the
        protected zone) in nautical miles.

        Arguments:
        - radius: The protected zone radius in nautical miles
        - acidx: Aircraft id(s) or group. When this argument is not provided the default PZ radius is changed.
          Otherwise the PZ radius for the passed aircraft is changed."""

        pass

    @stack.command(name="ADSBCD", brief="ADSBCD flag")
    def setrpz(self, flag: str = None):
        """Turn ON/OFF the Conflict Detection methods for ADS-B data."""

        # Convert string to bool if provided, else keep None
        bool_flag = None if flag is None else flag.lower() in ("1", "true", "yes", "on")
        self.cd_flag = not self.cd_flag if bool_flag is None else bool_flag

        return True, f"Conflict Detection for ADS-B is {self.cd_flag}."

    @stack.command(name="ADSBDTLOOK", brief="ADSBDTLOOK [time],[acid]")
    def setdtlook(self, time: "time" = -1.0, *acidx: "acid"):
        """Set the lookahead time (in [hh:mm:]sec) for conflict detection."""
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
