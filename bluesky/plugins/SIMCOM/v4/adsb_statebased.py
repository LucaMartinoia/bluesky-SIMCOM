import numpy as np
from bluesky import stack
from bluesky.tools import geo
from bluesky.tools.aero import nm
from bluesky.traffic.asas import ConflictDetection
import pyModeS as pms

"""State-based conflict detection based on ADS-B data."""


def init_plugin():
    """Plugin initialisation function."""

    print("SIMCOM: Loading ADS-B Conflict Detection method...")

    # Instantiate singleton entity
    adsbprotocol = ADSBCD()

    # Configuration parameters
    config = {
        "plugin_name": "ADSBCONFD",
        "plugin_type": "sim",
        # The update function is called after traffic is updated.
        # "update": adsbprotocol.update,
        # Reset contest
        # "reset": adsbprotocol.reset,
    }

    return config


class ADSBCD(ConflictDetection):
    def detect(self, ownship, intruder, rpz, hpz, dtlookahead):
        """Conflict detection between ownship (traf) and intruder (traf/adsb)."""
        # Identity matrix of order ntraf: avoid ownship-ownship detected conflicts
        n = len(ownship.id)
        I = np.eye(n)

        lat, lon, alt, gs, trk, vs = [], [], [], [], [], []
        # Decode using pyModeS
        for i in range(n):
            try:
                lat_i, lon_i = pms.adsb.airborne_position(
                    str(ownship.ADSBmsg_pos_e[i]),
                    str(ownship.ADSBmsg_pos_o[i]),
                    0,
                    1,
                )
                alt_i = pms.adsb.altitude(str(ownship.ADSBmsg_pos_e[i]))
                gs_i, trk_i, vs_i, _ = pms.adsb.velocity(str(ownship.ADSBmsg_v[i]))

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
            alt.append(alt_i)
            gs.append(gs_i)
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

        """
        IDEALLY, WE COULD CHANGE VARIABLES DEPENDING ON DISTANCE: FAR USE ADS-B, CLOSE USE GROUND TRUTH

        # Check if the AC are further apart than 3.5 NM
        far_mask = dist > (3.5 * nm)

        # INSTEAD OF OWNSHIP.ADSB* I WILL NEED TO EXTRACT THESE DATA FROM PYMODES AND THE ASSOCIATED MESSAGES
        lat_own = np.where(far_mask, ownship.ADSBlat, ownship.lat)
        lon_own = np.where(far_mask, ownship.ADSBlon, ownship.lon)
        lat_int = np.where(far_mask, intruder.ADSBlat, intruder.lat)
        lon_int = np.where(far_mask, intruder.ADSBlon, intruder.lon)
        alt_own = np.where(far_mask, ownship.ADSBalt, ownship.alt)
        alt_int = np.where(far_mask, intruder.ADSBalt, intruder.alt)

        trk_own = np.where(far_mask, ownship.ADSBtrk, ownship.trk)
        trk_int = np.where(far_mask, intruder.ADSBtrk, intruder.trk)
        gs_own = np.where(far_mask, ownship.ADSBgs, ownship.gs)
        gs_int = np.where(far_mask, intruder.ADSBgs, intruder.gs)
        """

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
        confpairs = [(ownship.id[i], ownship.id[j]) for i, j in zip(*np.where(swconfl))]
        swlos = (dist < rpz) * (np.abs(dalt) < hpz)
        lospairs = [(ownship.id[i], ownship.id[j]) for i, j in zip(*np.where(swlos))]

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
