from random import randint
import numpy as np
from types import SimpleNamespace
from bluesky import core, stack, traf  # , settings, navdb, sim, scr, tools
from bluesky.network.publisher import state_publisher
from bluesky.plugins.SIMCOM import adsb_encoder as encoder

"""SIMCOM plugin that implements the ADS-B protocol."""

"""TO DO:
1. Create ADSB POS function.
2. Add more labels versions with more data.
3. Add the line joining the ADS-B and true aircraft.

No idea why this implementation works, with @timed_function both in protocol.py and attacks.py, but it does.

There's a known bug: if I first create an AC and THEN load the plugin, it crashes. This happens because the
'with' statement append traf entries to the newly created self.attr. However, if the new attributes
are themselves traf.attr, it adds them again to traf, doubling the entries.

One possible fix is to:
1. Put a flag in create(n, new=True). If new=True, run super.create(), otherwise skip. This way, super.create() is called only on new AC and not on alraedy exising ACs.
2. At the end of __init__, if ntraf>0, call create(ntraf, False), so that if AC already exists their ADSB parameters are initialized.
3. Remove by hand the empty strings in the standard traf parameters (or at least in traf.id)
"""

# Type Codes for ADS-B messages.
# identification: 4. Identification is 1-4.
# position: 9. Airborne position is 9-18 (baro alt) or 20-22 (GNSS alt)
# velocity: 19, fixed.
type_codes = dict(identification=4, position=9, velocity=19)
ACUPDATE_RATE = 5  # Update rate of aircraft update messages [Hz]


def init_plugin():
    """Plugin initialisation function."""

    print("\n--- Loading SIMCOM plugin: ADS-B protocol ---\n")
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
        traf.ADSBmsg_pos_o[-n:] = [
            ADSB_position(traf.id[j], False) for j in range(-n, 0)
        ]
        traf.ADSBmsg_pos_e[-n:] = [
            ADSB_position(traf.id[j], True) for j in range(-n, 0)
        ]
        traf.ADSBmsg_id[-n:] = [ADSB_identification(traf.id[j]) for j in range(-n, 0)]
        traf.ADSBmsg_v[-n:] = [ADSB_velocity(traf.id[j]) for j in range(-n, 0)]

    @core.timed_function(dt=0.5, hook="update")  # runs every 0.5 simulated seconds
    def update(self):
        """If nothing strange is happening, the ADS-B data are updated based
        on the actual aircraft data. Otherwise, if there are cyber-attacks
        on the ADS-B protocol, the ADS-B data are determined by the attacks."""

        n = traf.ntraf

        traf.ADSBaltBaro[:n] = traf.alt[:n]
        traf.ADSBlat[:n] = traf.lat[:n]
        traf.ADSBlon[:n] = traf.lon[:n]
        traf.ADSBtas[:n] = traf.tas[:n]

        noise = np.random.uniform(-150, 150, size=n)
        traf.ADSBaltGNSS[:n] = np.maximum(traf.alt[:n] + noise, 0)

        traf.ADSBgsnorth[:n] = traf.gsnorth[:n]
        traf.ADSBgseast[:n] = traf.gseast[:n]
        traf.ADSBvs[:n] = traf.vs[:n]
        traf.ADSBhdg[:n] = traf.hdg[:n]
        traf.ADSBtrk[:n] = traf.trk[:n]

        # Compute ADS-B messages for all aircraft
        traf.ADSBmsg_pos_o[:n] = [ADSB_position(traf.id[i], False) for i in range(n)]
        traf.ADSBmsg_pos_e[:n] = [ADSB_position(traf.id[i], True) for i in range(n)]
        traf.ADSBmsg_id[:n] = [ADSB_identification(traf.id[i]) for i in range(n)]
        traf.ADSBmsg_v[:n] = [ADSB_velocity(traf.id[i]) for i in range(n)]

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

    @stack.command(name="STATUS", brief="STATUS acid, [status (0, 1, 2)]")
    def squawk(self, acid: "acid", status: str = ""):  # type: ignore
        """Set the surveillance status of a given aircraft. If the status is not given, it returns the status of the aircraft."""

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
    """Encode aircraft identification ADS-B
    message for given aircraft index."""

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
        capability, icao, type_codes["identification"], emitter_category, callsign
    )


def ADSB_position(acid: "acid", even: bool):  # type: ignore
    """Encode aircraft position ADS-B message for given aircraft index."""
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
        type_codes["position"],
        surveillance_status,
        antenna_flag,
        alt,
        time_bit,
        even,
        lat,
        lon,
    )


def ADSB_velocity(acid: "acid"):  # type: ignore
    """Encode aircraft position ADS-B message for given aircraft index."""
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
