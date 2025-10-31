import numpy as np
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

"""SIMCOM Plugin that implement GUI features related to ADS-B."""


def init_plugin():
    """Plugin initialisation function."""

    print("SIMCOM: Loading ADS-B GUI plugin...")

    # Configuration parameters
    config = {
        # The name of your plugin
        "plugin_name": "ADSBGUI",
        # The type of this plugin.
        "plugin_type": "gui",
    }
    # Start the new visual object
    stack.stack("LABEL 0")  # Hide the standard traffic labels
    addvisual("ADSBRADAR")  # Turn on the new overlay

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
    show_adsb_pz: ActData[bool] = ActData(False)
    show_lbl: bool = True
    show_traf: ActData[bool] = ActData(True)
    naircraft: ActData[int] = ActData(0)
    zoom: ActData[float] = ActData(1.0, group="panzoom")

    def __init__(self, parent):
        """Initialize the graphical objects and other variables."""

        super().__init__(parent=parent)
        # Initialize the counter that determines the update rate of the danger flashes
        self.counter = 0
        # The colors of the aircraft
        self.current_color = np.empty((self.naircraft, 4), dtype=np.uint8)
        self.initialized = False
        self.gt_lat = np.array([])
        self.gt_lon = np.array([])

        # Initialize the GPU buffers
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
        self.join_line = VertexArrayObject(gl.GL_LINES)

    def create(self):
        """Create the graphical objects."""

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

        # # --------Join line------------------------------------------------
        self.join_line.create(
            vertex=MAX_NAIRCRAFT * 4,
            color=palette.ADSBaircraft,
            usage=GLBuffer.UsagePattern.StreamDraw,
        )

        # Turn the initialization flag to True
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
        self.join_line.draw()

        self.shaderset.enable_wrap(True)
        # PZ circles only when they are bigger than the A/C symbols
        if self.show_adsb_pz and self.zoom >= 0.15:
            self.shaderset.set_vertex_scale_type(self.shaderset.VERTEX_IS_METERS)
            self.protectedzone.draw(n_instances=self.naircraft)

        # Draw traffic symbols
        self.shaderset.set_vertex_scale_type(self.shaderset.VERTEX_IS_SCREEN)
        self.ac_symbol.draw(n_instances=self.naircraft)

        if self.show_lbl:
            self.aclabels.draw(n_instances=self.naircraft)

    def _decode_adsb(self, data):
        """Decode the ADS-B data."""

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

        return lat, lon, alt, speed, track, callsigns

    # --------------------------------------------------------------------
    #                      SUBSCIBER FUNCTIONS
    # --------------------------------------------------------------------

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
            self.counter = 0
            return  # Not ready yet

        # Select the current gl surface
        self.glsurface.makeCurrent()
        # Update the number of AC
        self.naircraft = len(data.id)
        # Decode ADS-B data from messages [THIS CAN BE MOVED INSIDE THE FOR LOOP]
        lat, lon, alt, speed, track, callsigns = self._decode_adsb(data)

        # If there are aircraft
        if self.naircraft == 0:
            self.cpalines.set_vertex_count(0)
        else:
            # CPA lines to indicate conflicts
            ncpalines = np.count_nonzero(data.inconf)
            cpalines = np.zeros(4 * ncpalines, dtype=np.float32)
            self.cpalines.set_vertex_count(2 * ncpalines)
            confidx = 0
            # Aircraft color and label color
            color = np.empty((min(self.naircraft, MAX_NAIRCRAFT), 4), dtype=np.uint8)
            lblcolor = np.empty((min(self.naircraft, MAX_NAIRCRAFT), 4), dtype=np.uint8)
            # ADS-B label, conflict and join line
            rawlabel = ""
            joinlines = np.zeros(4 * self.naircraft, dtype=np.float32)
            # True aircraft data
            gt_lat = np.full(len(data.id), np.nan)
            gt_lon = np.full(len(data.id), np.nan)
            j = 0

            # Adjust the size of saved color arrays
            if len(self.current_color) < len(data.id):
                new_colors = np.tile(
                    palette.ADSBaircraft + (255,),
                    (len(data.id) - len(self.current_color), 1),
                )
                self.current_color = np.vstack([self.current_color, new_colors])
            elif len(self.current_color) > len(data.id):
                self.current_color = self.current_color[: len(data.id)]

            # Loop over all aircraft
            zdata = zip(
                data.id,
                callsigns,
                data.inconf,
                data.tcpamax,
                lat,
                lon,
                speed,
                track,
                data.status,
                data.attack,
            )
            for i, (
                acid,
                callsign,
                inconf,
                tcpa,
                lat0,
                lon0,
                gs,
                trk,
                sstatus,
                attack,
            ) in enumerate(zdata):
                if i >= MAX_NAIRCRAFT:
                    break

                # Pad the true data for GHOST aircraft
                if attack != "GHOST":
                    gt_lat[i] = self.gt_lat[j]
                    gt_lon[i] = self.gt_lon[j]
                    j += 1

                # First update the label
                if self.show_lbl:
                    # First 10 chars: acid, left-justified
                    rawlabel += f"{callsign[:8]:<10}"
                    # Next 10 chars: callsign inside parentheses, truncated/padded to 8 chars total
                    acid_str = f"({acid[:8]})"  # 8 chars callsign + 2 for parentheses = 10 chars
                    rawlabel += f"{acid_str:<10}"
                    # Final line: exactly 10 white spaces to pad/terminate cleanly
                    rawlabel += " " * 10

                if self.show_adsb:
                    # If not in conflict and not in danger, standard colors
                    if sstatus == 0:
                        rgb = palette.ADSBaircraft
                        # If both ADS-B and true aircraft are shown, reduce ADS-B alpha
                        if self.show_traf:
                            color[i, :] = tuple(rgb) + (120,)
                            self.current_color[i] = color[i]
                        elif not self.show_traf:
                            color[i, :] = tuple(rgb) + (255,)
                            self.current_color[i] = color[i]
                        # Label color still has max alpha
                        lblcolor[i, :] = tuple(rgb) + (255,)

                    else:
                        if self.counter % FLASH_MULT == 0:
                            # If green, make red
                            if np.all(
                                self.current_color[i, :3] == palette.ADSBaircraft
                            ):
                                color[i, :] = tuple(palette.ADSBdanger) + (255,)
                            else:  # If red, make green
                                color[i, :] = tuple(palette.ADSBaircraft) + (255,)
                            # Label is always green instead
                            lblcolor[i, :] = tuple(palette.ADSBaircraft) + (255,)
                            self.current_color[i] = color[i]
                        else:
                            color[i, :] = self.current_color[i, :]
                            lblcolor[i, :] = tuple(palette.ADSBaircraft) + (255,)

                    # If in conflict, compute CPA lines
                    if inconf:
                        color[i, :] = palette.conflict + (255,)
                        lblcolor[i, :] = palette.conflict + (255,)
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
                        self.current_color[i] = color[i]

                    # If both ADS-B and real aircraft are shown, draw join line
                    if self.show_traf and attack != "GHOST":
                        joinlines[4 * i : 4 * i + 4] = [
                            lat[i],
                            lon[i],
                            gt_lat[i],
                            gt_lon[i],
                        ]

            # Update buffers
            self.rpz.update(np.array(data.rpz, dtype=np.float32))
            self.lat.update(lat)
            self.lon.update(lon)
            self.hdg.update(track)
            self.alt.update(alt)
            self.join_line.update(vertex=joinlines)
            self.cpalines.update(vertex=cpalines)
            self.lblcolor.update(lblcolor)
            self.color.update(color)
            self.lbl.update(np.array(rawlabel.encode("utf8"), dtype=np.bytes_))

            # Update the counter
            self.counter += 1

    @subscriber(topic="ACDATA", actonly=True)
    def update_conflict_data(self, data):
        """Store true aircraft data used for the joinig line."""

        if not self.initialized:
            return
        if (
            ctx.action == ctx.action.Reset or ctx.action == ctx.action.ActChange
        ):  # TODO hack
            # Simulation reset: Clear all entries
            self.naircraft = 0
            return

        # True aircraft data
        self.gt_lat = np.array(data.lat, dtype=np.float32)
        self.gt_lon = np.array(data.lon, dtype=np.float32)

    # --------------------------------------------------------------------
    #                      STACK COMMANDS
    # --------------------------------------------------------------------

    @stack.command(name="SHOWDANGER", brief="SHOWDANGER [flag]")
    def showdanger(self, flag: str = None):
        """Toggle drawing of danger flashes."""

        # Convert string to bool if provided, else keep None
        bool_flag = None if flag is None else flag.lower() in ("1", "true", "yes", "on")
        self.show_danger = not self.show_danger if bool_flag is None else bool_flag

        return True, f"Show danger flashes {self.show_danger}."

    @stack.command(name="SHOWADSB", aliases=("SHOWADSBTRAF",), brief="SHOWADSB [flag]")
    def showadsbtraf(self, flag: str = None):
        """Toggle drawing of ADS-B traffic."""

        # Convert string to bool if provided, else keep None
        bool_flag = None if flag is None else flag.lower() in ("1", "true", "yes", "on")
        self.show_adsb = not self.show_adsb if bool_flag is None else bool_flag

        return True, f"Show ADS-B {self.show_adsb}."

    @stack.command(name="MGHOST", brief="MGHOST num")
    def mghost(self, num: int):
        """Creates n random GHOST aircraft on current screen."""

        # Pass the call to the stack, with the bound area given by the screen
        stack.forward(
            f'INSIDE {" ".join(str(el) for el in ref.area.bbox)} ATTACK MGHOST {num}'
        )

        return True

    @stack.command(name="SHOWADSBPZ", brief="SHOWADSBPZ [flag]")
    def showpz(self, flag: str = None):
        """Toggle drawing of aircraft protected zones."""

        # Convert string to bool if provided, else keep None
        bool_flag = None if flag is None else flag.lower() in ("1", "true", "yes", "on")
        self.show_adsb_pz = not self.show_adsb_pz if bool_flag is None else bool_flag

        return True, f"Show protected zones {self.show_adsb_pz}."
