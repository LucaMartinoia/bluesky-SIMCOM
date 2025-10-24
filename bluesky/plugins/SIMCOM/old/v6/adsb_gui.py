import numpy as np
from itertools import count
import pyModeS as pms

from bluesky import stack, settings, ref  # , settings, navdb, sim, scr, tools
from bluesky.ui.qtgl.glhelpers import (
    gl,
    RenderObject,
    VertexArrayObject,
    GLBuffer,
    Text,
    addvisual,
    Circle,
)
from bluesky.network.subscriber import subscriber
from bluesky.network.sharedstate import ActData
from bluesky.network import context as ctx
from bluesky.ui import palette
from bluesky.tools import geo
from bluesky.tools.aero import nm, kts

settings.set_variable_defaults(show_danger_traf=True, show_adsb_traf=True)

"""SIMCOM Plugin that implement GUI features related to ADS-B.

NEED TO ALSO ADD THE SSD CONFLICT LINES.
ALSO, NEED TO ADD THE TRAIL LINES."""


### Initialization function of your plugin. Do not change the name of this
### function, as it is the way BlueSky recognises this file as a plugin.
def init_plugin():
    """Plugin initialisation function."""

    print("SIMCOM: Loading ADS-B GUI plugin...")

    # Configuration parameters
    config = {
        # The name of your plugin
        "plugin_name": "ADSBGUI2",
        # The type of this plugin.
        "plugin_type": "gui",
    }
    # Start the new visual object
    stack.stack("LABEL 0")  # Hide the standard traffic labels
    addvisual("ADSBRADAR")
    stack.stack("CDMETHOD ADSBCD")

    print("SIMCOM: All ADS-B plugins loaded!")

    # init_plugin() should always return a configuration dict.
    return config


# Static defines
MAX_NAIRCRAFT = 10000
MAX_NCONFLICTS = 25000
MAX_ROUTE_LENGTH = 500
ROUTE_SIZE = 500
TRAILS_SIZE = 1000000
FLASH_MULT = 4  # The multiplier for the danger flashes

palette.set_default_colours(
    ADSBaircraft=(0, 255, 0),
    ADSBconflict=(255, 160, 0),
    ADSBdanger=(255, 0, 0),
)


class ADSBRadar(RenderObject, layer=101):
    """GUI for ADS-B traffic and danger screen flashes on radar."""

    # Per remote node attributes
    show_danger: bool = settings.show_danger_traf
    show_adsb: bool = settings.show_adsb_traf
    show_pz: ActData[bool] = ActData(False)
    ssd_all: ActData[bool] = ActData(False)
    show_lbl: bool = True
    naircraft: ActData[int] = ActData(0)
    zoom: ActData[float] = ActData(1.0, group="panzoom")

    def __init__(self, parent):
        super().__init__(parent=parent)
        # Initialize the counter that determines the update rate of the danger flashes
        self.counter = count(0)
        # The colors of the aircraft
        self.color_backup = np.empty((self.naircraft, 4), dtype=np.uint8)
        self.danger = np.zeros(self.naircraft, dtype=bool)  # The danger flags
        self.initialized = False
        self.gt_lat = np.array([])
        self.gt_lon = np.array([])
        self.gt_alt = np.array([])
        self.gt_gs = np.array([])
        self.gt_trk = np.array([])
        self.gt_rpz = np.array([])
        self.inconf = np.array([])
        self.tcpamax = np.array([])
        self.hdg = GLBuffer()
        self.lat = GLBuffer()
        self.lon = GLBuffer()
        self.alt = GLBuffer()
        self.rpz = GLBuffer()
        self.color = GLBuffer()
        self.lbl = GLBuffer()
        self.lblcolor = GLBuffer()

        self.ac_symbol = VertexArrayObject(gl.GL_TRIANGLE_FAN)
        self.protectedzone = Circle()
        self.cpalines = VertexArrayObject(gl.GL_LINES)
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
        self.rpz.create(MAX_NAIRCRAFT * 4, GLBuffer.UsagePattern.StreamDraw)

        # # --------Aircraft Proceted zone------------------------------------------------
        self.protectedzone.create(radius=0.5)
        self.protectedzone.set_attribs(
            lat=self.lat,
            lon=self.lon,
            scale=self.rpz,
            color=self.color,
            instance_divisor=1,
        )

        # # --------Aircraft Symbols and label--------------------------------------------
        acvertices = np.array(
            [
                (0.0, 0.5 * ac_size),
                (-0.5 * ac_size, -0.5 * ac_size),
                (0.0, -0.25 * ac_size),
                (0.5 * ac_size, -0.5 * ac_size),
            ],
            dtype=np.float32,
        )
        self.ac_symbol.create(vertex=acvertices)

        self.ac_symbol.set_attribs(
            lat=self.lat,
            lon=self.lon,
            color=self.color,
            orientation=self.hdg,
            instance_divisor=1,
        )

        self.aclabels.create(
            self.lbl,
            self.lat,
            self.lon,
            self.lblcolor,
            (-ac_size * 7.5, -0.5 * ac_size),
            instanced=True,
        )

        # # --------CPA lines------------------------------------------------
        self.cpalines.create(
            vertex=MAX_NCONFLICTS * 16,
            color=palette.ADSBconflict,
            usage=GLBuffer.UsagePattern.StreamDraw,
        )

        self.initialized = True

    def draw(self):
        """Draw all traffic graphics."""

        # Get data for active node
        if self.naircraft == 0 or not self.show_adsb:
            return

        # Send the (possibly) updated global uniforms to the buffer
        self.shaderset.set_vertex_scale_type(self.shaderset.VERTEX_IS_LATLON)
        self.shaderset.enable_wrap(False)

        self.cpalines.draw()

        self.shaderset.enable_wrap(True)
        # PZ circles only when they are bigger than the A/C symbols
        if self.show_pz and self.zoom >= 0.15:
            self.shaderset.set_vertex_scale_type(self.shaderset.VERTEX_IS_METERS)
            self.protectedzone.draw(n_instances=self.naircraft)

        # Draw traffic symbols
        self.shaderset.set_vertex_scale_type(self.shaderset.VERTEX_IS_SCREEN)
        self.ac_symbol.draw(n_instances=self.naircraft)

        if self.show_lbl:
            self.aclabels.draw(n_instances=self.naircraft)

    def update_colors(self, data):
        """Update aircraft color based on danger flags. Flashes red every 4 ticks."""

        def color_switch(flag, color):
            print("color switch:", color, palette.ADSBaircraft)
            return (
                palette.ADSBdanger
                if flag and np.all(color == palette.ADSBaircraft)
                else palette.ADSBaircraft
            )

        def color_array_update():
            print("color backup:", self.color_backup)
            print(self.color_backup)
            old = self.color_backup
            new = np.empty((self.naircraft, 4), dtype=np.uint8)

            if len(old) == len(new):
                return old
            elif len(old) < len(new):
                new[: len(old), :] = old
            else:  # len(old) > len(new)
                new = old[: len(new), :]
            return new

        if next(self.counter) % FLASH_MULT == 0:
            self.danger = np.array(data.status != 0, dtype=bool)
            self.color_backup = color_array_update()

            for idx, flag in enumerate(self.danger):
                print("color backup2:", self.color_backup)
                self.color_backup[idx, :] = color_switch(
                    flag, self.color_backup[idx, :]
                )

            self.color.update(self.color_backup)

    @subscriber(topic="ADSBDATA", actonly=True)
    def update_adsb_data(self, data):
        """Update GPU buffers with ADS-B data and danger flags."""

        if not self.initialized:
            return
        if (
            ctx.action == ctx.action.Reset or ctx.action == ctx.action.ActChange
        ):  # TODO hack
            # Simulation reset: Clear all entries
            self.naircraft = 0
            self.counter = count(0)
            return  # Not ready yet

        self.glsurface.makeCurrent()

        self.naircraft = len(data.id)

        lat, lon, alt, speed, track, callsigns = [], [], [], [], [], []

        # Decode using pyModeS
        for i in range(self.naircraft):
            try:
                lat_i, lon_i = pms.adsb.airborne_position(
                    str(data.ADSBmsg_pos_e[i]),
                    str(data.ADSBmsg_pos_o[i]),
                    0,
                    1,
                )
                alt_i = pms.adsb.altitude(str(data.ADSBmsg_pos_e[i]))
                speed_i, track_i = pms.adsb.speed_heading(str(data.ADSBmsg_v[i]))
                callsign_i = pms.adsb.callsign(str(data.ADSBmsg_id[i])).strip("_")

            except Exception:
                lat_i, lon_i, alt_i, speed_i, track_i, callsign_i = (
                    np.nan,
                    np.nan,
                    np.nan,
                    np.nan,
                    np.nan,
                    "",
                )

            lat.append(lat_i)
            lon.append(lon_i)
            alt.append(alt_i)
            speed.append(speed_i * kts)
            track.append(track_i)
            callsigns.append(callsign_i)

        # Convert after the loop
        lat = np.array(lat, dtype=np.float32)
        lon = np.array(lon, dtype=np.float32)
        alt = np.array(alt, dtype=np.float32)
        speed = np.array(speed, dtype=np.float32)
        track = np.array(track, dtype=np.float32)
        callsigns = np.array(callsigns, dtype=object)

        if self.naircraft == 0:
            self.cpalines.set_vertex_count(0)
        else:
            # Update data in GPU buffer
            self.rpz.update(self.gt_rpz)
            self.lat.update(lat)
            self.lon.update(lon)
            self.hdg.update(track)
            self.alt.update(alt)

            # CPA lines to indicate conflicts
            ncpalines = np.count_nonzero(self.inconf)

            cpalines = np.zeros(4 * ncpalines, dtype=np.float32)
            self.cpalines.set_vertex_count(2 * ncpalines)

            color = np.empty((min(self.naircraft, MAX_NAIRCRAFT), 4), dtype=np.uint8)

            # Updating the ADS-B label
            rawlabel = ""
            confidx = 0

            # Necessary if the standard conflict detection method is used
            if len(self.inconf) < len(data.id):
                self.inconf = np.pad(
                    self.inconf, (0, len(data.id) - len(self.inconf)), "constant"
                )
            if len(self.tcpamax) < len(data.id):
                self.tcpamax = np.pad(
                    self.tcpamax, (0, len(data.id) - len(self.tcpamax)), "constant"
                )

            zdata = zip(
                data.id, callsigns, self.inconf, self.tcpamax, lat, lon, speed, track
            )
            for i, (acid, callsign, inconf, tcpa, lat0, lon0, gs, trk) in enumerate(
                zdata
            ):
                if i >= MAX_NAIRCRAFT:
                    break

                if self.show_lbl:
                    # First 10 chars: acid, left-justified
                    rawlabel += f"{callsign[:8]:<10}"

                    # Next 10 chars: callsign inside parentheses, truncated/padded to 8 chars total
                    acid_str = f"({acid[:8]})"  # 8 chars callsign + 2 for parentheses = 10 chars
                    rawlabel += f"{acid_str:<10}"

                    # Final line: exactly 10 white spaces to pad/terminate cleanly
                    rawlabel += " " * 10

                if inconf:
                    color[i, :] = palette.conflict + (255,)
                    lat1, lon1 = geo.qdrpos(
                        lat0,
                        lon0,
                        trk,
                        tcpa * gs / nm,
                    )
                    cpalines[4 * confidx : 4 * confidx + 4] = [
                        lat0,
                        lon0,
                        lat1,
                        lon1,
                    ]
                    confidx += 1
                else:
                    rgb = palette.ADSBaircraft
                    color[i, :] = tuple(rgb) + (255,)

            self.cpalines.update(vertex=cpalines)

            self.lblcolor.update(color)
            self.color.update(color)
            self.lbl.update(np.array(rawlabel.encode("utf8"), dtype=np.bytes_))

    @subscriber(topic="ACDATA", actonly=True)
    def update_conflict_data(self, data):
        """Update GPU buffers with new aircraft simulation data."""
        if not self.initialized:
            return
        if (
            ctx.action == ctx.action.Reset or ctx.action == ctx.action.ActChange
        ):  # TODO hack
            # Simulation reset: Clear all entries
            self.naircraft = 0
            return

        self.inconf = np.array(data.inconf, dtype=np.float32)
        self.tcpamax = np.array(data.tcpamax, dtype=np.float32)
        self.gt_rpz = np.array(data.rpz, dtype=np.float32)

    @stack.command(name="SHOWDANGER", brief="SHOWDANGER [flag]")
    def showdanger(self, flag: str = None):
        """Toggle drawing of danger flashes."""

        # Convert string to bool if provided, else keep None
        bool_flag = None if flag is None else flag.lower() in ("1", "true", "yes", "on")
        self.show_danger = not self.show_danger if bool_flag is None else bool_flag

    @stack.command(name="SHOWADSB", aliases=("SHOWADSBTRAF",), brief="SHOWADSB [flag]")
    def showadsbtraf(self, flag: str = None):
        """Toggle drawing of ADS-B traffic."""

        # Convert string to bool if provided, else keep None
        bool_flag = None if flag is None else flag.lower() in ("1", "true", "yes", "on")
        self.show_adsb = not self.show_adsb if bool_flag is None else bool_flag

    @stack.command(name="MGHOST", brief="MGHOST num")
    def mghost(self, num: int):
        """Creates n random GHOST aircraft on current screen."""

        stack.forward(
            f'INSIDE {" ".join(str(el) for el in ref.area.bbox)} ATTACK MGHOST {num}'
        )

    @stack.command(name="SHOWADSBPZ", brief="MGHOST num")
    def showpz(self, flag: bool = None):
        """Toggle drawing of aircraft protected zones."""
        pass
