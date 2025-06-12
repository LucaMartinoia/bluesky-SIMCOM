""" This plugin implements the ADS-B protocol. """

from random import randint
import numpy as np
# Import the global bluesky objects. Uncomment the ones you need
from bluesky import core, stack, traf  #, settings, navdb, sim, scr, tools
from bluesky.tools.aero import ft
from . import adsbencoder as encoder

### Initialization function of your plugin. Do not change the name of this
### function, as it is the way BlueSky recognises this file as a plugin.

# Generic ADS-B data fields valid (for the moment) for all aircraft. We assume ADS-B version 1.
# capability = 5 means 'Aircraft with level 2 transponder, airborne'.
capability = 5
# Type Codes for ADS-B messages.
# identification: 4. Identification is 1-4. 4 is associated with standard aircraft, with emitter category identifies the wake vertex category.
# position: 20. Airborne position is 9-18 (barometric altitude) or 20-22 (GNSS altitude). 20 is associated with high accuracy.
type_codes = dict(identification=4, position=9)
# Emitter category: combined with type code, it tells the wake vertex category. TC=4, EC=3 means medium aircraft. ############################################# TO DO, CHECK IF THE DATA EXISTS IN OPENAP OR LEGACY.
emitter_category = 3
time_bit = 0 # Time bit used for position messages, keep 0 for all messages for the moment.
surveillance_status = 0 # Indicates ALERT status. 0 indicates no alert.
# The antenna flag indicates whether the system has a single antenna or two antennas (1: single antenna).
antenna_flag = 1 # In Version 2, this is the NICb bit.

# These squawk values are reserved for danger situations (hijack, generic problems).
DANGER_SQUAWKS = {'7500', '7600', '7700'}

def init_plugin():
    ''' Plugin initialisation function. '''

    print("\n--- Loading SIMCOM plugin: ADS-B protocol ---\n")
    # Instantiate our example entity
    adsbprotocol = ADSBprotocol()
    
    # Configuration parameters
    config = {
        # The name of your plugin
        'plugin_name':     'ADSBPROTOCOL',
        # The type of this plugin. For now, only simulation plugins are possible.
        'plugin_type':     'sim',
        }
    # init_plugin() should always return a configuration dict.

    return config


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


### Entities in BlueSky are objects that are created only once (called singleton)
### which implement some traffic or other simulation functionality.
### To define an entity that ADDS functionality to BlueSky, create a class that
### inherits from bluesky.core.Entity.
### To replace existing functionality in BlueSky, inherit from the class that
### provides the original implementation (see for example the asas/eby plugin).
class ADSBprotocol(core.Entity):
    
    def __init__(self):
        super().__init__()
        # All classes deriving from Entity can register lists and numpy arrays
        # that hold per-aircraft data. This way, their size is automatically
        # updated when aircraft are created or deleted in the simulation.
        with self.settrafarrays():
            self.altADSB = np.array([])
            self.icao = np.array([], dtype='U6')
            self.squawk = np.array([], dtype='U4')
            self.danger = np.array([], dtype=bool)

    def create(self, n=1):
        ''' This function gets called automatically when new aircraft are created.'''
        # Don't forget to call the base class create when you reimplement this function!
        super().create(n)
        # Initialize ADS-B altitudes to match real ones on aircraft creation
        self.altADSB[-n:] = traf.alt[-n:]

        icaos = np.array([f'{x:06X}' for x in np.random.randint(0, 0xFFFFFF + 1, size=n)])
        self.icao[-n:] = icaos

        # Create the squawk codes
        squawks = [f'{randint(0, 0o7777):04o}' for _ in range(n)]
        self.squawk[-n:] = squawks          

        # Set danger flags element-wise for the new aircraft
        self.danger[-n:] = np.isin(self.squawk[-n:], list(DANGER_SQUAWKS))

        
        if n == 1:
            stack.stack(f'ECHO ADS-B altitude for {traf.id[-1]} set to {self.altADSB[-1]/ft:.0f} ft')
            stack.stack(f'ECHO True altitude {traf.alt[-1]/ft:.0f} ft')
            stack.stack(f'ECHO ICAO address for {traf.id[-1]} is {self.icao[-1]}')
            stack.stack(f'ECHO The squawk code for {traf.id[-1]} is set to {self.squawk[-1]}.')
            stack.stack(f'ECHO ADS-B identification message: {self.ADSB_identification(traf.id[-1])}')
            stack.stack(f'ECHO ADS-B position even message: {self.ADSB_position(traf.id[-1], True)}')
            stack.stack(f'ECHO ADS-B position odd message: {self.ADSB_position(traf.id[-1], False)}')
            stack.stack('ECHO')


    def ADSB_identification(self, acid: 'acid'):
        '''Encode aircraft identification ADS-B message for given aircraft index.'''
        index = self.id2idx(acid)
        icao = self.icao[index]
        callsign = traf.id[index][:8].upper().ljust(8)
        if len(traf.id[index])>8:
            stack.stack(f'ECHO WARNING: Callsign {traf.id[index]} too long, truncating to 8 characters')

        # Encode and return hex string
        return encoder.identification(capability, icao, type_codes['identification'], emitter_category, callsign) # identification(ca: int, icao: str, tc: int, ec: int, callsign: str)

    def ADSB_position(self, acid: 'acid', even: bool):
        '''Encode aircraft identification ADS-B message for given aircraft index.'''
        index = self.id2idx(acid)
        icao = self.icao[index]
        lat = traf.lat[index]
        lon = traf.lon[index]
        alt = self.altADSB[index]

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
            
    # Functions that need to be called periodically can be indicated to BlueSky
    # with the timed_function decorator
    @core.timed_function(name='danger', dt=2)
    def update(self):
        ''' Periodic update function that flashes when aircraft is in danger.'''
        dangerous_ids = np.array(traf.id)[self.danger]   # mask traf.id with danger flags
        dangerous_squawks = self.squawk[self.danger]
        
        for acid, squawk in zip(dangerous_ids, dangerous_squawks):
            stack.stack(f'ECHO --- Aircraft {acid} is in danger (squawk {squawk}) ---')

    @stack.command
    def altADSB(self, acid: 'acid', alt: 'alt' = -1):
        ''' Set the altitude that is broacast to the ADS-B of a given aircraft to "alt".'''
        if alt < 0:
            return True, f'Aircraft {traf.id[acid]} ADS-B altitude is {self.altADSB[acid]/ft:0f} ft.'
            
        self.altADSB[acid] = alt
        return True, f'ADS-B altitude for {traf.id[acid]} set to {alt/ft:.0f} ft.'

    @stack.command
    def squawk(self, acid: 'acid', squawk: str = ''):
        ''' Set the squawk code of a given aircraft.'''
        if squawk == '':
            return True, f'Aircraft {traf.id[acid]} squawk code is {self.squawk[acid]}.'
    
        if not is_valid_squawk(squawk):
            return False, f'Invalid squawk code {squawk}. Must be an integer between 0000 and 7777.'
        
        self.squawk[acid] = squawk
        self.danger[acid] = self.squawk[acid] in DANGER_SQUAWKS

        return True, f'The squawk code for {traf.id[acid]} is set to {squawk}.'