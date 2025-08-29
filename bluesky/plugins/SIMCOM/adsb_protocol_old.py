""" SIMCOM plugin that implements the ADS-B protocol. """

""" TO DO: update function, POS function, labels! """

from random import randint
import numpy as np
from types import SimpleNamespace
# Import the global bluesky objects. Uncomment the ones you need
from bluesky import core, stack, traf  #, settings, navdb, sim, scr, tools
from bluesky.tools.aero import ft
from bluesky.network.publisher import state_publisher
from . import adsb_encoder as encoder
from . import adsb_attacks as attacks

### Initialization function of your plugin. Do not change the name of this
### function, as it is the way BlueSky recognises this file as a plugin.

# Type Codes for ADS-B messages.
# identification: 4. Identification is 1-4. 4 is associated with standard aircraft, with emitter category identifies the wake vertex category.
# position: 9. Airborne position is 9-18 (barometric altitude) or 20-22 (GNSS altitude). 9 is associated with high accuracy.
type_codes = dict(identification=4, position=9, velocity=19)
DANGER_SQUAWKS = {'7500', '7600', '7700'}  # These squawk values are reserved for danger situations (hijack, generic problems).
ACUPDATE_RATE = 5  # Update rate of aircraft update messages [Hz]
_adsbprotocol = None  # Define the singleton globally
# Map each attack type to a handler
handlers = {
    'none': attacks.normal,
    'jamming': attacks.jamming,
}

def get_adsbprotocol():
    ''' This function returns the singleton-instance of adsbprotocol.
    Used by other "sim" plugins to interact with ADS-B data.'''
    return _adsbprotocol

"""
def init_plugin():
    ''' Plugin initialisation function. '''

    print("\n--- Loading SIMCOM plugin: ADS-B protocol ---\n")
    # Instantiate our example entity
    global _adsbprotocol  # refer to the global variable
    _adsbprotocol = ADSBprotocol()
    
    # Configuration parameters
    config = {
        # The name of your plugin
        'plugin_name': 'ADSBPROTOCOL',
        # The type of this plugin.
        'plugin_type': 'sim',
        # The update function is called after traffic is updated.
        'update': _adsbprotocol.update,
        # Reset contest
        'reset': _adsbprotocol.reset
        }
    # init_plugin() should always return a configuration dict.
    return config
"""


def is_valid_squawk(squawk):
    if not isinstance(squawk, str):
        return False
    if len(squawk) != 4:
        return False
    try:
        value = int(squawk, 8)  # Parse as octal
        return 0 <= value <= 0o7777
    except ValueError:
        return False


### Need some way to still identify AC uniquely:
### the ACID still remains the main identifier.
class ADSBprotocol(core.Entity):
    
    def __init__(self):
        super().__init__()

        # All classes deriving from Entity can register lists and numpy arrays
        # that hold per-aircraft data. This way, their size is automatically
        # updated when aircraft are created or deleted in the simulation.
        with self.settrafarrays():
            self.attack       = np.array([], dtype='<U16')
            self.ADSBicao     = np.array([], dtype='U6')
            self.ADSBcallsign = []  # identifier (string)
            self.ADSBsquawk   = np.array([], dtype='U4')
            self.ADSBdanger   = np.array([], dtype=bool)
            self.ADSBaltGNSS  = np.array([], dtype=float)  # for velocity messages we need barometric and GNSS altitude. Assume the one already implemented is baro and implement the other with some gaussian error around the main value.
            self.ADSBaltBaro  = np.array([], dtype=float)  # for velocity messages we need barometric and GNSS altitude. Assume the one already implemented is baro and implement the other with some gaussian error around the main value.
            self.ADSBlat      = np.array([])  # latitude [deg]
            self.ADSBlon      = np.array([])  # longitude [deg]
            self.ADSBtas      = np.array([])  # true airspeed [m/s]
            self.ADSBgsnorth  = np.array([])  # ground speed [m/s]
            self.ADSBgseast   = np.array([])  # ground speed [m/s]
            self.ADSBvs       = np.array([])  # vertical speed [m/s]
            self.ADSBhdg      = np.array([])  # traffic heading [deg]
            self.ADSBtrk      = np.array([])  # track angle [deg]
            self.ADSBcapability          = np.array([], dtype=int)  # capability = 5 means 'Aircraft with level 2 transponder, airborne'.
            self.ADSBemitter_category    = np.array([], dtype=int)  # Emitter category: combined with type code, it tells the wake vertex category. TC=4, EC=3 means medium aircraft.
            self.ADSBtime_bit            = np.array([], dtype=int)  # Time bit used for position messages, keep 0 for all messages for the moment.
            self.ADSBsurveillance_status = np.array([], dtype=int)  # Indicates ALERT status. 0 indicates no alert.
            self.ADSBantenna_flag        = np.array([], dtype=int)  # The antenna flag indicates whether the system has a single antenna or two antennas (1: single antenna). In Version 2, this is the NICb bit.
            self.ADSBintent_change       = np.array([], dtype=int)  # Intent change flag
            self.ADSBNACv                = np.array([], dtype=int)  # Navication accuracy category - velocity (3 bits)

    def create(self, n=1):
        ''' This function gets called automatically when new aircraft are created.'''
        # Don't forget to call the base class create when you reimplement this function!
        super().create(n)

        # Initialize the attack flags
        self.attack[-n:] = ['none']*n
        
        # Inizialize the ICAO addresses and call sign
        icaos = np.array([f'{x:06X}' for x in np.random.randint(0, 0xFFFFFF + 1, size=n)])
        self.ADSBicao[-n:] = icaos
        self.ADSBcallsign[-n:] = traf.id[-n:]

        # Create the squawk codes
        squawks = [f'{randint(0, 0o7777):04o}' for _ in range(n)]
        self.ADSBsquawk[-n:] = squawks

        # Set danger flags element-wise for the new aircraft
        self.ADSBdanger[-n:] = np.isin(self.ADSBsquawk[-n:], list(DANGER_SQUAWKS))
        
        # Initialize GNSS altitudes to match real ones on aircraft creation
        noise = np.random.uniform(-150, 150, size=n)
        GNSSalt = traf.alt[-n:] + noise
        self.ADSBaltGNSS[-n:] = np.maximum(GNSSalt, 0)
        self.ADSBaltBaro[-n:] = traf.alt[-n:]

        # Inizialize the position and velocity
        self.ADSBlat[-n:]      = traf.lat[-n:]
        self.ADSBlon[-n:]      = traf.lon[-n:]
        self.ADSBtas[-n:]      = traf.tas[-n:]
        self.ADSBgsnorth[-n:]  = traf.gsnorth[-n:]
        self.ADSBgseast[-n:]   = traf.gseast[-n:]
        self.ADSBvs[-n:]       = traf.vs[-n:]
        self.ADSBhdg[-n:]      = traf.hdg[-n:]
        self.ADSBtrk[-n:]      = traf.trk[-n:]

        # Capability (CA) field, Emitter category, time bit
        self.ADSBcapability[-n:]          = 5
        self.ADSBemitter_category[-n:]    = 3  ############################################# TO DO, CHECK IF THE DATA EXISTS IN OPENAP OR LEGACY.
        self.ADSBtime_bit[-n:]            = 0
        self.ADSBsurveillance_status[-n:] = 0
        self.ADSBantenna_flag[-n:]        = 1
        self.ADSBintent_change[-n:]       = 0
        self.ADSBNACv[-n:]                = 2

        if n == 1:
            stack.stack(f'ECHO ICAO address for {self.ADSBcallsign[-1]} is {self.ADSBicao[-1]}')
            stack.stack(f'ECHO The squawk code for {self.ADSBcallsign[-1]} is set to {self.ADSBsquawk[-1]}.')
            stack.stack('ECHO')

    def update(self):
        """If nothing strange is happening, the ADS-B data are updated based on the actual
        aircraft data. Otherwise, if there are cyber-attacks on the ADS-B protocol, the
        ADS-B data are determined by the attacks."""

        for attack_type, func in handlers.items():
            mask = self.attack == attack_type  # Define the mask for vectorialized computations

            # Subset of real traffic values
            traf_data = SimpleNamespace(
                alt=traf.alt[mask],
                lat=traf.lat[mask],
                lon=traf.lon[mask],
                tas=traf.tas[mask],
                gsnorth=traf.gsnorth[mask],
                gseast=traf.gseast[mask],
                vs=traf.vs[mask],
                hdg=traf.hdg[mask],
                trk=traf.trk[mask]
            )
        
            # Subset of current ADS-B values
            adsb_data = SimpleNamespace(
                GNSSalt=self.ADSBaltGNSS[mask],
                BaroAlt=self.ADSBaltBaro[mask],
                lat=self.ADSBlat[mask],
                lon=self.ADSBlon[mask],
                tas=self.ADSBtas[mask],
                gsnorth=self.ADSBgsnorth[mask],
                gseast=self.ADSBgseast[mask],
                vs=self.ADSBvs[mask],
                hdg=self.ADSBhdg[mask],
                trk=self.ADSBtrk[mask]
            )
            result = func(traf_data, adsb_data)
            
            self.ADSBaltGNSS[mask] = result['GNSSalt']
            self.ADSBaltBaro[mask] = result['BaroAlt']
            self.ADSBlat[mask]     = result['lat']
            self.ADSBlon[mask]     = result['lon']
            self.ADSBtas[mask]     = result['tas']
            self.ADSBgsnorth[mask] = result['gsnorth']
            self.ADSBgseast[mask]  = result['gseast']
            self.ADSBvs[mask]      = result['vs']
            self.ADSBhdg[mask]     = result['hdg']
            self.ADSBtrk[mask]     = result['trk']
        
    def reset(self):
        ''' Clear all traffic data upon simulation reset. '''
        # Some child reset functions depend on a correct value of self.ntraf
        self.ntraf = 0
        # This ensures that the traffic arrays (which size is dynamic)
        # are all reset as well, so all lat,lon,sdp etc but also objects adsb
        super().reset()


    # --------------------------------------------------------------------
    #                      ADS-B WRAPPER FUNCTIONS
    # --------------------------------------------------------------------

    def ADSB_identification(self, acid: 'acid'):
        '''Encode aircraft identification ADS-B message for given aircraft index.'''
        index = self.id2idx(acid)
        
        capability = self.ADSBcapability[index]
        icao = self.ADSBicao[index]
        emitter_category = self.ADSBemitter_category[index]
        callsign = self.ADSBcallsign[index][:8].upper().ljust(8)
        if len(self.ADSBcallsign[index])>8:
            stack.stack(f'ECHO WARNING: Callsign {self.ASDBcallsign[index]} too long, truncating to 8 characters')

        # Encode and return hex string
        return encoder.identification(5, icao, type_codes['identification'], emitter_category, callsign) # identification(ca: int, icao: str, tc: int, ec: int, callsign: str)

    def ADSB_position(self, acid: 'acid', even: bool):
        '''Encode aircraft identification ADS-B message for given aircraft index.'''
        index = self.id2idx(acid)
        
        capability = self.ADSBcapability[index]
        icao = self.ADSBicao[index]
        surveillance_status = self.ADSBsurveillance_status[index]
        antenna_flag = self.ADSBantenna_flag[index]
        alt = self.ADSBaltBaro[index]
        time_bit = self.ADSBtime_bit[index]
        lat = self.ADSBlat[index]
        lon = self.ADSBlon[index]

        # Encode and return hex string
        return encoder.position(capability, icao, type_codes['position'], surveillance_status, antenna_flag, alt, time_bit, even, lat, lon) # position(ca: int, icao: str, TC: int, status: int, antenna: int, alt: float, time: int, even: bool, lat: float, lon: float)

    
    def id2idx(self, acid):
        """Find index of aircraft id"""
        if not isinstance(acid, str):
            # id2idx is called for multiple id's
            # Fast way of finding indices of all ACID's in a given list
            tmp = dict((v, i) for i, v in enumerate(traf.id))
            # return [tmp.get(acidi, -1) for acidi in acid]
        else:
             # Catch last created id (* or # symbol)
            if acid in ('#', '*'):
                return traf.ntraf - 1

            try:
                return traf.id.index(acid.upper())
            except:
                return -1
            
    @state_publisher(topic='ADSBDATA', dt=1000 // ACUPDATE_RATE)
    def send_aircraft_data(self):
        data = dict()

        data['id']                = traf.id
        data['icao']              = self.ADSBicao
        data['callsign']          = self.ADSBcallsign
        data['squawk']            = self.ADSBsquawk
        data['danger']            = self.ADSBdanger
        data['altBaro']           = self.ADSBaltBaro
        data['lat']               = self.ADSBlat
        data['lon']               = self.ADSBlon
        data['hdg']               = self.ADSBhdg
        data['trk']               = self.ADSBtrk

        return data

    # --------------------------------------------------------------------
    #                      STACK COMMANDS
    # --------------------------------------------------------------------
    
    @stack.command(name='GNSSALT', aliases=('GNSSALTITUDE', ), brief='GNSSALT acid, [alt]')
    def GNSSalt(self, acid: 'acid', alt: 'alt' = -1):
        ''' Set the GNSS altitude of a given aircraft to "alt". If "alt" is negative or None, it returns the ADS-B altitude of the aircraft. '''
        if alt < 0:
            return True, f'Aircraft {traf.id[acid]} ADS-B altitude is {self.ADSBaltGNSS[acid]/ft:0f} ft.'
            
        self.ADSBaltGNSS[acid] = alt
        return True, f'ADS-B altitude for {traf.id[acid]} set to {alt/ft:.0f} ft.'

    
    @stack.command(name='SQUAWK', brief='SQUAWK acid, [squawk]')
    def squawk(self, acid: 'acid', squawk: str = ''):
        ''' Set the squawk code of a given aircraft. If the code is not given, it returns the squawk code of the aircraft. '''
        if squawk == '':
            return True, f'Aircraft {traf.id[acid]} squawk code is {self.ADSBsquawk[acid]}.'
    
        if not is_valid_squawk(squawk):
            return False, f'Invalid squawk code {squawk}. Must be an integer between 0000 and 7777.'
        
        self.ADSBsquawk[acid] = squawk
        self.ADSBdanger[acid] = self.ADSBsquawk[acid] in DANGER_SQUAWKS

        return True, f'The squawk code for {traf.id[acid]} is set to {squawk}.'

    
    @stack.command(name='ATTACK', brief='ATTACK acid, attack_type (none, jammed)')
    def attack(self, acid: 'acid', attack: str = ''):
        ''' Set the attack for a given aircraft. '''
        if attack.lower() in ['off', 'clear']:
            attack = 'none'
            
        if attack == '':
            return True, f'Aircraft {traf.id[acid]} is currently under {self.attack[acid]} attack.'
        
        self.attack[acid] = attack
    
        return True, f'{traf.id[acid]} is under {attack} attack.'