from random import randint
import numpy as np
# Import the global bluesky objects. Uncomment the ones you need
from bluesky import core, stack, traf  #, settings, navdb, sim, scr, tools
from bluesky.tools.aero import ft
from bluesky.network.publisher import state_publisher
from bluesky.network.subscriber import subscriber
from . import adsb_encoder as encoder

# These squawk values are reserved for danger situations (hijack, generic problems).
DANGER_SQUAWKS = {'7500', '7600', '7700'}

# Update rate of aircraft update messages [Hz]
ACUPDATE_RATE = 5

def init_plugin():
    ''' Plugin initialisation function. '''

    print("\n--- Loading SIMCOM plugin: ADS-B attacks ---\n")

    # Instantiate our example entity
    adsbattacks = ADSBattacks()
    
    # Configuration parameters
    config = {
        # The name of your plugin
        'plugin_name': 'ADSBATTACKS',
        # The type of this plugin.
        'plugin_type': 'sim',
        # Reset contest
        'reset': adsbattacks.reset
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


### Need some way to still identify AC uniquely:
### the ACID still remains the main identifier.
class ADSBattacks(core.Entity):
    
    def __init__(self):
        super().__init__()
        # All classes deriving from Entity can register lists and numpy arrays
        # that hold per-aircraft data. This way, their size is automatically
        # updated when aircraft are created or deleted in the simulation.
        with self.settrafarrays():
            self.attack = []

            
    def create(self, n=1):
        ''' This function gets called automatically when new aircraft are created.'''
        # Don't forget to call the base class create when you reimplement this function!
        super().create(n)

        # Inizialize the ICAO addresses
        self.attack[-n:] = ''

    
    def reset(self):
        ''' Clear all traffic data upon simulation reset. '''
        # Some child reset functions depend on a correct value of self.ntraf
        self.ntraf = 0
        # This ensures that the traffic arrays (which size is dynamic)
        # are all reset as well, so all lat,lon,sdp etc but also objects adsb
        super().reset()

        self.attack = []


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
            
    @subscriber(topic='ADSBDATA', actonly=False, from_group='*', to_group='*')
    def update_adsb_data(self, data):
        """ NO IDEA WHY THIS DOES NOT WORK """
        print("subscriber")