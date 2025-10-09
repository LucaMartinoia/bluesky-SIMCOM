from random import randint
import numpy as np
from types import SimpleNamespace
from bluesky import core, stack, traf  # , settings, navdb, sim, scr, tools
from bluesky.network.publisher import state_publisher
from bluesky.plugins.SIMCOM import adsb_encoder as encoder
from .adsb_attacks import attack_types

"""SIMCOM plugin that implements the ADS-B protocol."""

"""TO DO: Implement data-exchange based on ADS-B protocol encoding."""

# Type Codes for ADS-B messages.
# identification: 4. Identification is 1-4. 4 is associated with
# standard aircraft, with emitter category identifies the wake vertex category.
# position: 9. Airborne position is 9-18 (barometric altitude) or
# 20-22 (GNSS altitude). 9 is associated with high accuracy.
type_codes = dict(identification=4, position=9, velocity=19)
DANGER_SQUAWKS = {  # DELETE THIS
    "7500",
    "7600",
    "7700",
}  # These squawk values are reserved for danger situations.
ACUPDATE_RATE = 5  # Update rate of aircraft update messages [Hz]

"""
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

        # Apply normal behaviour to safe aircraft
        mask = traf.ADSBattack == "NONE"
        traf.ADSBaltBaro[mask] = traf.alt[mask]
        traf.ADSBlat[mask] = traf.lat[mask]
        traf.ADSBlon[mask] = traf.lon[mask]
        traf.ADSBtas[mask] = traf.tas[mask]
        noise = np.random.uniform(-150, 150, size=np.sum(mask))
        traf.ADSBaltGNSS[mask] = np.maximum(traf.alt[mask] + noise, 0)
        traf.ADSBgsnorth[mask] = traf.gsnorth[mask]
        traf.ADSBgseast[mask] = traf.gseast[mask]
        traf.ADSBvs[mask] = traf.vs[mask]
        traf.ADSBhdg[mask] = traf.hdg[mask]
        traf.ADSBtrk[mask] = traf.trk[mask]

        idxs = np.where(mask)[0]
        # compute ADS-B messages
        traf.ADSBmsg_pos_o[idxs] = [self.ADSB_position(traf.id[i], False) for i in idxs]
        traf.ADSBmsg_pos_e[idxs] = [self.ADSB_position(traf.id[i], True) for i in idxs]
        traf.ADSBmsg_id[idxs] = [self.ADSB_identification(traf.id[i]) for i in idxs]
        traf.ADSBmsg_v[idxs] = [self.ADSB_velocity(traf.id[i]) for i in idxs]
"""

def is_valid_squawk(squawk):  ## TO DELETE
    if not isinstance(squawk, str):
        return False
    if len(squawk) != 4:
        return False
    try:
        value = int(squawk, 8)  # Parse as octal
        return 0 <= value <= 0o7777
    except ValueError:
        return False


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
            traf.ADSBsquawk = np.array([], dtype="U4")
            traf.ADSBdanger = np.array([], dtype=bool)
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
            # CA = 5 means 'Aircraft with level 2 transponder, airborne'.
            traf.ADSBcapability = np.array([], dtype=int)
            traf.ADSBemitter_category = np.array([], dtype=int)
            # Time bit for position messages
            traf.ADSBtime_bit = np.array([], dtype=int)
            # ALERT status (0: no alert)
            traf.ADSBsurveillance_status = np.array([], dtype=int)
            # Antenna flag (1: single antenna)
            traf.ADSBantenna_flag = np.array([], dtype=int)
            traf.ADSBintent_change = np.array([], dtype=int)  # IC flag
            # Navigation accuracy category - velocity
            traf.ADSBNACv = np.array([], dtype=int)

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

        # Create the squawk codes
        squawks = [f"{randint(0, 0o7777):04o}" for _ in range(n)]
        traf.ADSBsquawk[-n:] = squawks

        # Set danger flags element-wise for the new aircraft
        traf.ADSBdanger[-n:] = np.isin(traf.ADSBsquawk[-n:], DANGER_SQUAWKS)

        # Initialize GNSS altitudes to match real ones on aircraft creation
        noise = np.random.uniform(-150, 150, size=n)
        GNSSalt = traf.alt[-n:] + noise
        traf.ADSBaltGNSS[-n:] = np.maximum(GNSSalt, 0)
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
        traf.ADSBcapability[-n:] = 5
        # TODO: check if this data exists in OpenAP or legacy source
        traf.ADSBemitter_category[-n:] = 3
        traf.ADSBtime_bit[-n:] = 0
        traf.ADSBsurveillance_status[-n:] = 0
        traf.ADSBantenna_flag[-n:] = 1
        traf.ADSBintent_change[-n:] = 0
        traf.ADSBNACv[-n:] = 2

    @core.timed_function(dt=0.5)  # runs every 0.5 simulated seconds
    def update(self):
        """If nothing strange is happening, the ADS-B data are updated based
        on the actual aircraft data. Otherwise, if there are cyber-attacks
        on the ADS-B protocol, the ADS-B data are determined by the attacks."""

        # Apply normal behaviour to safe aircraft...
        mask = traf.ADSBattack == "NONE"
        traf.ADSBaltBaro[mask] = traf.alt[mask]
        traf.ADSBlat[mask] = traf.lat[mask]
        traf.ADSBlon[mask] = traf.lon[mask]
        traf.ADSBtas[mask] = traf.tas[mask]
        noise = np.random.uniform(-150, 150, size=np.sum(mask))
        traf.ADSBaltGNSS[mask] = np.maximum(traf.alt[mask] + noise, 0)
        traf.ADSBgsnorth[mask] = traf.gsnorth[mask]
        traf.ADSBgseast[mask] = traf.gseast[mask]
        traf.ADSBvs[mask] = traf.vs[mask]
        traf.ADSBhdg[mask] = traf.hdg[mask]
        traf.ADSBtrk[mask] = traf.trk[mask]

        # ... apply attacks to the rest
        for attack_type, func in attack_types.items():
            mask = traf.ADSBattack == attack_type
            if not np.any(mask):
                continue  # Skip if no aircraft under this attack

            # Get acids for aircraft under this attack
            acids = np.array(traf.id)[mask]

            # Compute ADS-B messages for these aircraft
            adsb_position_even = [self.ADSB_position(acid, even=True) for acid in acids]
            adsb_position_odd = [self.ADSB_position(acid, even=False) for acid in acids]
            adsb_identification = [self.ADSB_identification(acid) for acid in acids]

            # Call the attack function
            result = func(
                adsb_position_even=adsb_position_even,
                adsb_position_odd=adsb_position_odd,
                adsb_identification=adsb_identification,
                mask=mask,
            )

    def reset(self):
        """Clear all traffic data upon simulation reset."""

        # Some child reset functions depend on a correct value of self.ntraf
        self.ntraf = 0
        # This ensures that the traffic arrays (which size is dynamic)
        # are all reset as well, so all lat,lon,sdp etc but also objects adsb
        super().reset()

    # --------------------------------------------------------------------
    #                      ADS-B WRAPPER FUNCTIONS
    # --------------------------------------------------------------------

    def ADSB_identification(self, acid: "acid"):  # type: ignore
        """Encode aircraft identification ADS-B
        message for given aircraft index."""

        index = self.id2idx(acid)

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

    def ADSB_position(self, acid: "acid", even: bool):  # type: ignore
        """Encode aircraft position ADS-B message for given aircraft index."""

        index = self.id2idx(acid)

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

    def id2idx(self, acid):
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

    @state_publisher(topic="ADSBDATA", dt=1000 // ACUPDATE_RATE)
    def send_aircraft_data(self):
        data = dict()

        data["id"] = traf.id
        data["icao"] = traf.ADSBicao
        data["callsign"] = traf.ADSBcallsign
        data["squawk"] = traf.ADSBsquawk
        data["danger"] = traf.ADSBdanger
        data["altBaro"] = traf.ADSBaltBaro
        data["lat"] = traf.ADSBlat
        data["lon"] = traf.ADSBlon
        data["hdg"] = traf.ADSBhdg
        data["trk"] = traf.ADSBtrk

        return data

    # --------------------------------------------------------------------
    #                      STACK COMMANDS
    # --------------------------------------------------------------------

    @stack.command(name="SQUAWK", brief="SQUAWK acid, [squawk]")
    def squawk(self, acid: "acid", squawk: str = ""):  # type: ignore
        """Set the squawk code of a given aircraft. If the code is not given, it returns the squawk code of the aircraft."""

        if squawk == "":
            return (
                True,
                f"Aircraft {traf.id[acid]} squawk code is {traf.ADSBsquawk[acid]}.",
            )

        if not is_valid_squawk(squawk):
            return (
                False,
                f"Invalid squawk code {squawk}. Must be an integer between 0000 and 7777.",
            )

        traf.ADSBsquawk[acid] = squawk
        traf.ADSBdanger[acid] = traf.ADSBsquawk[acid] in DANGER_SQUAWKS

        return True, f"The squawk code for {traf.id[acid]} is set to {squawk}."
