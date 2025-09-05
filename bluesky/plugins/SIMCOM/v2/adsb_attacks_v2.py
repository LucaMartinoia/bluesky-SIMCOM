from random import randint
import numpy as np
# Import the global bluesky objects. Uncomment the ones you need
from bluesky import core, stack, traf  #, settings, navdb, sim, scr, tools
from bluesky.tools.aero import ft
from bluesky.network.publisher import state_publisher
from bluesky.network.subscriber import subscriber

attack_types = ['NONE', 'JAMMING']
ACUPDATE_RATE = 5  # Update rate of aircraft update messages [Hz]

'''
def init_plugin():
    ''' Plugin initialisation function. '''
    # Instantiate our example entity
    adsbattacks = ADSBattacks()
    
    # Configuration parameters
    config = {
        # The name of your plugin
        'plugin_name': 'ADSBATTACK_v2',
        # The type of this plugin.
        'plugin_type': 'sim'
        }
    # init_plugin() should always return a configuration dict.
    return config


class ADSBattacks(core.Entity):

    def __init__(self):
        super().__init__()
                
    @subscriber(topic='ADSBATTACKS', broadcast=True, actonly=False, raw=False, from_group='*')
    def update_adsb_attacks(self, data):
        print("subscriber",data)
        print('')

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