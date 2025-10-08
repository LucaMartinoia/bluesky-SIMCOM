from random import randint
import numpy as np
# Import the global bluesky objects. Uncomment the ones you need
from bluesky import core, stack, traf  #, settings, navdb, sim, scr, tools
from bluesky.tools.aero import ft

attack_types = {}

def normal(traf_data, adsb_data):
    ''' Normal behaviour, no attacks. '''
    noise = np.random.uniform(-150, 150, size=traf_data.alt.shape)
    GNSSalt = traf_data.alt + noise
    
    result = {
        'GNSSalt': np.maximum(GNSSalt, 0),  # GNSS altitude
        'BaroAlt': traf_data.alt,           # Barometric altitude (same as actual)
        'lat':     traf_data.lat,
        'lon':     traf_data.lon,
        'tas':     traf_data.tas,
        'gsnorth': traf_data.gsnorth,
        'gseast':  traf_data.gseast,
        'vs':      traf_data.vs,
        'hdg':     traf_data.hdg,
        'trk':     traf_data.trk,
    }
    return result

def jamming(traf_data, adsb_data):
    ''' Simulate jamming by freezing ADS-B outputs to last known values. '''
    
    result = {
        'GNSSalt': adsb_data.GNSSalt,
        'BaroAlt': adsb_data.BaroAlt,
        'lat':     adsb_data.lat,
        'lon':     adsb_data.lon,
        'tas':     adsb_data.tas,
        'gsnorth': adsb_data.gsnorth,
        'gseast':  adsb_data.gseast,
        'vs':      adsb_data.vs,
        'hdg':     adsb_data.hdg,
        'trk':     adsb_data.trk,
    }

    return result

attack_types.update({
    'NONE': normal,
    'JAMMING': jamming
})


# --------------------------------------------------------------------
#                      STACK COMMANDS
# --------------------------------------------------------------------

@stack.command(name='ATTACK', brief='ATTACK acid, attack_type (NONE, JAMMING)')
def attack(acid: 'acid', attack: str=''):
    '''Set the attack for a given aircraft.'''
    if attack.upper() in ['OFF', 'CLEAR']:
        attack = 'NONE'

    if attack == '':
        return True, f'Aircraft {traf.id[acid]} is currently under {traf.ADSBattack[acid]} attack.'

    attack = attack.upper()
    if attack not in attack_types:
        return False, f'Unknown attack type "{attack}". Supported types: {", ".join(attack_types.keys())}.'

    traf.ADSBattack[acid] = attack
    return True, f'{traf.id[acid]} is under {attack} attack.'