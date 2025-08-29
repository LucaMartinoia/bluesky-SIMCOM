""" SIMCOM Plugin that implement GUI features related to ADS-B. """
import numpy as np
from itertools import count
# Import the global bluesky objects. Uncomment the ones you need
from bluesky import core, stack, ui, settings  #, settings, navdb, sim, scr, tools
from bluesky.ui.qtgl.glhelpers import gl, RenderObject, VertexArrayObject, GLBuffer, Text, addvisual
from bluesky.network.subscriber import subscriber
from bluesky.network.sharedstate import ActData
from bluesky.network import context as ctx

settings.set_variable_defaults(show_danger_traf = True, show_adsb_traf = True)

### Initialization function of your plugin. Do not change the name of this
### function, as it is the way BlueSky recognises this file as a plugin.
def init_plugin():
    ''' Plugin initialisation function. '''
    
    # Configuration parameters
    config = {
        # The name of your plugin
        'plugin_name':     'ADSBGUI',
        # The type of this plugin.
        'plugin_type':     'gui',
        }
    # Start the new visual object
    stack.stack('LABEL 0')  # Hide the standard traffic labels
    addvisual("ADSBRADAR")

    # init_plugin() should always return a configuration dict.
    return config


MAX_NAIRCRAFT = 10000
red_clr = np.array((255, 0, 0, 255), dtype=np.uint8)
green_clr = np.array((0, 255, 0, 255), dtype=np.uint8)
flash_mult = 4  # The multiplier for the danger flashes.

### Entities in BlueSky are objects that are created only once (called singleton)
### which implement some traffic or other simulation functionality.
### To define an entity that ADDS functionality to BlueSky, create a class that
### inherits from bluesky.core.Entity.
### To replace existing functionality in BlueSky, inherit from the class that
### provides the original implementation (see for example the asas/eby plugin).
class ADSBRadar(RenderObject, layer=101):
    ''' GUI for ADS-B traffic and danger screen flashes on radar. '''
    # Per remote node attributes
    show_danger: bool = settings.show_danger_traf
    show_adsb: bool = settings.show_adsb_traf
    show_lbl: bool = True
    naircraft: ActData[int] = ActData(0)
    
    def __init__(self, parent):
        super().__init__(parent=parent)
        self.counter = count(0)  # Initialize the counter that determines the update rate of the danger flashes
        self.color_backup = np.empty((self.naircraft, 4), dtype=np.uint8)  # The colors of the aircraft
        self.danger = np.zeros(self.naircraft, dtype=bool)  # The danger flags
        self.initialized = False
        self.hdg = GLBuffer()
        self.lat = GLBuffer()
        self.lon = GLBuffer()
        self.alt = GLBuffer()
        self.color = GLBuffer()
        self.lbl = GLBuffer()
        self.lblcolor = GLBuffer()

        self.ac_symbol = VertexArrayObject(gl.GL_TRIANGLE_FAN)
        self.aclabels = Text(settings.text_size, (10, 3))

    def create(self):
        ac_size = settings.ac_size
        self.hdg.create(MAX_NAIRCRAFT * 4, GLBuffer.UsagePattern.StreamDraw)
        self.lat.create(MAX_NAIRCRAFT * 4, GLBuffer.UsagePattern.StreamDraw)
        self.lon.create(MAX_NAIRCRAFT * 4, GLBuffer.UsagePattern.StreamDraw)
        self.alt.create(MAX_NAIRCRAFT * 4, GLBuffer.UsagePattern.StreamDraw)
        self.color.create(MAX_NAIRCRAFT * 4, GLBuffer.UsagePattern.StreamDraw)
        self.lbl.create(MAX_NAIRCRAFT * 24, GLBuffer.UsagePattern.StreamDraw)
        self.lblcolor.create(MAX_NAIRCRAFT * 4, GLBuffer.UsagePattern.StreamDraw)

        acvertices = np.array([(0.0, 0.5 * ac_size), (-0.5 * ac_size, -0.5 * ac_size),
                               (0.0, -0.25 * ac_size), (0.5 * ac_size, -0.5 * ac_size)],
                              dtype=np.float32)
        self.ac_symbol.create(vertex=acvertices)

        self.ac_symbol.set_attribs(lat=self.lat, lon=self.lon, color=self.color,
                                   orientation=self.hdg, instance_divisor=1)

        self.aclabels.create(self.lbl, self.lat, self.lon, self.lblcolor,
                             (ac_size, -0.5 * ac_size), instanced=True)

        self.initialized = True

    def draw(self):
        ''' Draw all traffic graphics. '''
        # Get data for active node
        if self.naircraft == 0 or not self.show_adsb:
            return

        # Draw traffic symbols
        self.ac_symbol.draw(n_instances=self.naircraft)

        if self.show_lbl:
            self.aclabels.draw(n_instances=self.naircraft)


    def update_colors(self, data):
        ''' Update aircraft color based on danger flags. Flash red every 4 ticks. '''
        def color_switch(flag, color):
            return red_clr if flag and np.all(color == green_clr) else green_clr
    
        def color_array_update():
            old = self.color_backup
            new = np.empty((self.naircraft, 4), dtype=np.uint8)
    
            if len(old) == len(new):
                return old
            elif len(old) < len(new):
                new[:len(old), :] = old
            else:  # len(old) > len(new)
                new = old[:len(new), :]
            return new
    
        if next(self.counter) % flash_mult == 0:
            self.danger = np.array(data.danger, dtype=bool)
            self.color_backup = color_array_update()
    
            for idx, flag in enumerate(self.danger):
                self.color_backup[idx, :] = color_switch(flag, self.color_backup[idx, :])
    
            self.color.update(self.color_backup)
        
    @subscriber(topic='ADSBDATA', actonly=True)
    def update_adsb_data(self, data):
        ''' Update GPU buffers with ADS-B data and danger flags. '''
        if not self.initialized:
            return
        if ctx.action == ctx.action.Reset or ctx.action == ctx.action.ActChange:# TODO hack
            # Simulation reset: Clear all entries
            self.naircraft = 0
            self.counter = count(0)
            return  # Not ready yet

        self.glsurface.makeCurrent()

        self.naircraft = len(data.lat)

        # Update data in GPU buffers
        self.lat.update(np.array(data.lat, dtype=np.float32))
        self.lon.update(np.array(data.lon, dtype=np.float32))
        self.hdg.update(np.array(data.trk, dtype=np.float32))
        self.alt.update(np.array(data.altBaro, dtype=np.float32))

        # Update color depending on danger logic
        self.update_colors(data)

        # Updating the ADS-B label
        rawlabel = ''
        zdata = zip(data.id, data.callsign)
        for i, (acid, callsign) in enumerate(zdata):
            if i >= MAX_NAIRCRAFT:
                break
            if self.show_lbl:
                # First 10 chars: acid, left-justified
                rawlabel += f'{callsign[:8]:<10}'
            
                # Next 10 chars: callsign inside parentheses, truncated/padded to 8 chars total
                acid_str = f'({acid[:8]})'  # 8 chars callsign + 2 for parentheses = 10 chars
                rawlabel += f'{acid_str:<10}'

                # Final line: exactly 10 white spaces to pad/terminate cleanly
                rawlabel += ' ' * 10

        self.lblcolor.update(green_clr)
        self.lbl.update(np.array(rawlabel.encode('utf8'), dtype=np.bytes_))



    #### THESE FUNCTIONS AREN'T WORKING PROPERLY ####
    @stack.command(name='SHOWDANGER', aliases=('SHOWDANGERFLASH',), brief='SHOWDANGER [flag]')
    def showdanger(self, flag: bool=None):
        ''' Toggle drawing of danger flashes. '''
        self.show_danger = not self.show_danger if flag is None else flag

    @stack.command(name='SHOWDADSB', aliases=('SHOWADSBTRAF',), brief='SHOWADSB [flag]')
    def showadsbtraf(self, flag: bool=None):
        ''' Toggle drawing of ADS-B traffic. '''
        self.show_adsb = not self.show_adsb if flag is None else flag