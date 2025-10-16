import numpy as np
from bluesky import core, stack, traf  # , settings, navdb, sim, scr, tools
from bluesky.network.publisher import state_publisher
from bluesky.plugins.SIMCOM import adsb_encoder as encoder

"""SIMCOM plugin that implements the ADS-B protocol.

No idea why this implementation works, with @timed_function both in protocol.py and attacks.py, but it does.
"""

# Type Codes for ADS-B messages.
# identification: 4. Identification is 1-4.
# position: 9. Airborne position is 9-18 (baro alt) or 20-22 (GNSS alt)
# velocity: 19, fixed.
TYPE_CODES = dict(identification=4, position=9, velocity=19)
ACUPDATE_RATE = 5  # Update rate of aircraft update messages [Hz]
ADSB_UPDATE = 0.5  # Update dt for ADS-B messages [s]


def init_plugin():
    """Plugin initialisation function."""

    print("SIMCOM: Loading ADS-B protocol plugin...")

    # Instantiate singleton entity
    adsbprotocol = ADSBprotocol()

    # Configuration parameters
    config = {
        "plugin_name": "ADSBPROTOCOL",
        "plugin_type": "sim",
        # The update function is called after traffic is updated.
        # "update": adsbprotocol.update,
        # Reset contest
        "reset": adsbprotocol.reset,
    }

    return config


# Need some way to still identify AC uniquely:
# the ACID still remains the main identifier.
class ADSBprotocol(core.Entity):

    def __init__(self):
        super().__init__()

        # All classes deriving from Entity can register lists and numpy arrays
        # that hold per-aircraft data. This way, their size is automatically
        # updated when aircraft are created or deleted in the simulation.
        with traf.settrafarrays():
            traf.ADSBattack = np.array([], dtype="<U16")
            traf.ADSBicao = np.array([], dtype="U6")
            traf.ADSBcallsign = []  # identifier (string)
            traf.ADSBaltGNSS = np.array([], dtype=float)
            traf.ADSBaltBaro = np.array([], dtype=float)
            traf.ADSBlat = np.array([])  # latitude [deg]
            traf.ADSBlon = np.array([])  # longitude [deg]
            traf.ADSBtas = np.array([])  # true airspeed [m/s]
            traf.ADSBgsnorth = np.array([])  # ground speed [m/s]
            traf.ADSBgseast = np.array([])  # ground speed [m/s]
            traf.ADSBvs = np.array([])  # vertical speed [m/s]
            traf.ADSBhdg = np.array([])  # traffic heading [deg]
            traf.ADSBtrk = np.array([])  # track angle [deg]
            # Capability field
            traf.ADSBcapability = np.array([], dtype=int)
            traf.ADSBemitter_category = np.array([], dtype=int)
            traf.ADSBtime_bit = np.array([], dtype=int)  # Time bit
            # ALERT status (0: no alert)
            traf.ADSBsurveillance_status = np.array([], dtype=int)
            traf.ADSBantenna_flag = np.array([], dtype=int)
            traf.ADSBintent_change = np.array([], dtype=int)  # IC flag
            traf.ADSBNACv = np.array([], dtype=int)
            # ADS-B messages
            traf.ADSBmsg_pos_o = np.array([], dtype="<U28")
            traf.ADSBmsg_pos_e = np.array([], dtype="<U28")
            traf.ADSBmsg_id = np.array([], dtype="<U28")
            traf.ADSBmsg_v = np.array([], dtype="<U28")

    def create(self, n=1):
        """This function gets called automatically
        when new aircraft are created."""

        super().create(n)

        # Initialize the attack flags
        traf.ADSBattack[-n:] = ["NONE"] * n

        # Inizialize the ICAO addresses and call sign
        icaos = np.array(
            [f"{x:06X}" for x in np.random.randint(0, 0xFFFFFF + 1, size=n)]
        )
        traf.ADSBicao[-n:] = icaos
        traf.ADSBcallsign[-n:] = traf.id[-n:]

        # Initialize altitudes to match real ones on aircraft creation
        noise = np.random.uniform(-150, 150, size=n)
        traf.ADSBaltGNSS[-n:] = np.maximum(traf.alt[-n:] + noise, 0)
        traf.ADSBaltBaro[-n:] = traf.alt[-n:]

        # Inizialize the position and velocity
        traf.ADSBlat[-n:] = traf.lat[-n:]
        traf.ADSBlon[-n:] = traf.lon[-n:]
        traf.ADSBtas[-n:] = traf.tas[-n:]
        traf.ADSBgsnorth[-n:] = traf.gsnorth[-n:]
        traf.ADSBgseast[-n:] = traf.gseast[-n:]
        traf.ADSBvs[-n:] = traf.vs[-n:]
        traf.ADSBhdg[-n:] = traf.hdg[-n:]
        traf.ADSBtrk[-n:] = traf.trk[-n:]

        # Capability (CA) field, Emitter category, time bit
        # CA = 5 means 'Aircraft with level 2 transponder, airborne'.
        traf.ADSBcapability[-n:] = 5
        # TODO: check if this data exists in OpenAP or legacy source
        traf.ADSBemitter_category[-n:] = 3
        traf.ADSBtime_bit[-n:] = 0
        traf.ADSBsurveillance_status[-n:] = 0
        traf.ADSBantenna_flag[-n:] = 1
        traf.ADSBintent_change[-n:] = 0
        traf.ADSBNACv[-n:] = 2

        # Initialize ADS-B messages
        for j in range(-n, 0):
            traf.ADSBmsg_pos_o[j] = ADSB_position(traf.id[j], False)
            traf.ADSBmsg_pos_e[j] = ADSB_position(traf.id[j], True)
            traf.ADSBmsg_id[j] = ADSB_identification(traf.id[j])
            traf.ADSBmsg_v[j] = ADSB_velocity(traf.id[j])

    @core.timed_function(
        dt=ADSB_UPDATE, hook="update"
    )  # runs every 0.5 simulated seconds
    def ADSBupdate(self):
        """the ADS-B data are updated based on the actual aircraft data, except for
        GHOST aircraft."""

        n = traf.ntraf
        # GHOST aircraft do not have real data to update ADS-B fields
        mask = traf.ADSBattack != "GHOST"
        indices = np.where(mask)[0]

        traf.ADSBaltBaro[mask] = traf.alt[:n]
        traf.ADSBlat[mask] = traf.lat[:n]
        traf.ADSBlon[mask] = traf.lon[:n]
        traf.ADSBtas[:n] = traf.tas[:n]

        noise = np.random.uniform(-150, 150, size=n)
        traf.ADSBaltGNSS[mask] = np.maximum(traf.alt[:n] + noise, 0)

        traf.ADSBgsnorth[mask] = traf.gsnorth[:n]
        traf.ADSBgseast[mask] = traf.gseast[:n]
        traf.ADSBvs[mask] = traf.vs[:n]
        traf.ADSBhdg[mask] = traf.hdg[:n]
        traf.ADSBtrk[:n] = traf.trk[:n]

        # Compute ADS-B messages for all aircraft
        for i in indices:
            traf.ADSBmsg_pos_o[i] = ADSB_position(traf.id[i], False)
            traf.ADSBmsg_pos_e[i] = ADSB_position(traf.id[i], True)
            traf.ADSBmsg_id[i] = ADSB_identification(traf.id[i])
            traf.ADSBmsg_v[i] = ADSB_velocity(traf.id[i])

    def reset(self):
        """Clear all traffic data upon simulation reset."""

        # Some child reset functions depend on a correct value of self.ntraf
        traf.ntraf = 0
        # This ensures that the traffic arrays (which size is dynamic)
        # are all reset as well, so all lat,lon,sdp etc but also objects adsb
        super().reset()

    # --------------------------------------------------------------------
    #                      PUBLISHER AND UTILS
    # --------------------------------------------------------------------

    @state_publisher(topic="ADSBDATA", dt=1000 // ACUPDATE_RATE)
    def send_aircraft_data(self):
        """Broadcast ADS-B data to the GPU for displaying.
        The update rate is higher than in real world, so it includes dead reckoning.

        The id is to keep track of AC, the status is not necessary in theory
        (contained in velocity messages), but in practice pyModeS has no way
        to extract the status field from ADS-B messages."""

        data = dict()

        data["id"] = traf.id
        data["status"] = traf.ADSBsurveillance_status
        data["ADSBmsg_pos_o"] = traf.ADSBmsg_pos_o
        data["ADSBmsg_pos_e"] = traf.ADSBmsg_pos_e
        data["ADSBmsg_id"] = traf.ADSBmsg_id
        data["ADSBmsg_v"] = traf.ADSBmsg_v

        return data

    # --------------------------------------------------------------------
    #                      STACK COMMANDS
    # --------------------------------------------------------------------

    @stack.command(name="STATUS", brief="STATUS acid,[status (0, 1, 2)]")
    def squawk(self, acid: "acid", status: str = ""):  # type: ignore
        """Set the surveillance status of a given aircraft.
        If the status is not given, it returns the status of the aircraft."""

        if status == "":
            return (
                True,
                f"Aircraft {traf.id[acid]} surveillance status is {traf.ADSBsurveillance_status[acid]}.",
            )

        traf.ADSBsurveillance_status[acid] = int(status)

        return True, f"The surveillance status for {traf.id[acid]} is set to {status}."


# --------------------------------------------------------------------
#                      ADS-B WRAPPER FUNCTIONS
# --------------------------------------------------------------------


def ADSB_identification(acid: "acid"):  # type: ignore
    """Encode identification ADS-B message for given aircraft index."""

    index = id2idx(acid)

    capability = traf.ADSBcapability[index]
    icao = traf.ADSBicao[index]
    emitter_category = traf.ADSBemitter_category[index]
    callsign = traf.ADSBcallsign[index][:8].upper().ljust(8)
    if len(traf.ADSBcallsign[index]) > 8:
        stack.stack(
            f"ECHO WARNING: Callsign {traf.ADSBcallsign[index]} too long,truncating to 8 characters"
        )

    # Encode and return hex string
    return encoder.identification(
        capability, icao, TYPE_CODES["identification"], emitter_category, callsign
    )


def ADSB_position(acid: "acid", even: bool):  # type: ignore
    """Encode position ADS-B message for given aircraft index."""

    index = id2idx(acid)

    capability = traf.ADSBcapability[index]
    icao = traf.ADSBicao[index]
    surveillance_status = traf.ADSBsurveillance_status[index]
    antenna_flag = traf.ADSBantenna_flag[index]
    alt = traf.ADSBaltBaro[index]
    time_bit = traf.ADSBtime_bit[index]
    lat = traf.ADSBlat[index]
    lon = traf.ADSBlon[index]

    # Encode and return hex string
    return encoder.position(
        capability,
        icao,
        TYPE_CODES["position"],
        surveillance_status,
        antenna_flag,
        alt,
        time_bit,
        even,
        lat,
        lon,
    )


def ADSB_velocity(acid: "acid"):  # type: ignore
    """Encode velocity ADS-B message for given aircraft index."""

    index = id2idx(acid)

    capability = traf.ADSBcapability[index]
    icao = traf.ADSBicao[index]
    ic_flag = traf.ADSBintent_change[index]
    NACv = traf.ADSBNACv[index]
    gs_north = traf.ADSBgsnorth[index]
    gs_east = traf.ADSBgseast[index]
    vert_src = 1
    s_vert = traf.ADSBvs[index]
    GNSS_alt = traf.ADSBaltGNSS[index]
    baro_alt = traf.ADSBaltBaro[index]

    # Encode and return hex string
    return encoder.velocity(
        capability,
        icao,
        ic_flag,
        NACv,
        gs_north,
        gs_east,
        vert_src,
        s_vert,
        GNSS_alt,
        baro_alt,
    )


def id2idx(acid):
    """Find index of aircraft id."""

    if not isinstance(acid, str):
        # id2idx is called for multiple id's
        # Fast way of finding indices of all ACID's in a given list
        tmp = dict((v, i) for i, v in enumerate(traf.id))
        # return [tmp.get(acidi, -1) for acidi in acid]
    else:
        # Catch last created id (* or # symbol)
        if acid in ("#", "*"):
            return traf.ntraf - 1

        try:
            return traf.id.index(acid.upper())
        except:
            return -1
