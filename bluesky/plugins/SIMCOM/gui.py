import numpy as np
from bluesky import stack, settings, ref
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
from bluesky.tools.aero import nm, kts, ft

settings.set_variable_defaults(show_danger_traf=True, show_adsb_traf=True)

"""
Plugin that implements GUI features related to ADS-B traffic.
"""


def init_plugin():
    """Plugin initialisation function."""

    # Configuration parameters
    config = {
        # The name of your plugin
        "plugin_name": "ADSBVIEW",
        # The type of this plugin.
        "plugin_type": "gui",
    }
    # Start the new visual object
    addvisual("ADSBVIEW")  # Turn on the new overlay
    stack.stack("TOGGLEVIEW 3")  # Turn on ADS-B + traffic view

    return config


# Static defines
MAX_NAIRCRAFT = 1000
MAX_NCONFLICTS = 2500
FLASH_MULT = 2  # The multiplier for the danger flashes

palette.set_default_colours(
    ADSBaircraft=(0, 255, 0),
    ADSBconflict=(255, 160, 0),
    ADSBdanger=(255, 0, 0),
)


class ADSBview(RenderObject, layer=101):
    """
    GUI for ADS-B traffic on radar screen.
    """

    # Per remote node attributes
    show_danger: bool = settings.show_danger_traf
    show_adsb: bool = settings.show_adsb_traf
    show_adsb_pz: ActData[bool] = ActData(False)
    show_lbl: bool = True
    show_traf: ActData[bool] = ActData(True)
    naircraft: ActData[int] = ActData(0)
    zoom: ActData[float] = ActData(1.0, group="panzoom")

    def __init__(self, parent):
        super().__init__(parent=parent)
        # Initialize the counter that determines the update rate of the danger flashes
        self.counter = 0
        # The colors of the aircraft
        self.current_color = np.empty((self.naircraft, 4), dtype=np.uint8)
        # Status of the GUI
        self.initialized = False
        # Transition level for altitude labels
        self.translvl = 5000.0 * ft

        # Initialize the GPU buffers
        self.hdg = GLBuffer()
        self.lat = GLBuffer()
        self.lon = GLBuffer()
        self.alt = GLBuffer()
        self.rpz = GLBuffer()
        self.color = GLBuffer()
        self.lbl = GLBuffer()
        self.lblcolor = GLBuffer()

        self.ac_symbol = VertexArrayObject(gl.GL_TRIANGLE_FAN)  # type:ignore
        self.protectedzone = Circle()
        self.cpalines = VertexArrayObject(gl.GL_LINES)  # type:ignore
        self.aclabels = Text(settings.text_size, (10, 4))
        self.joinline = VertexArrayObject(gl.GL_LINES)  # type:ignore

    def create(self):
        """
        Create the graphical objects.
        """

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
            color=palette.ADSBconflict,  # type:ignore
            usage=GLBuffer.UsagePattern.StreamDraw,
        )

        # # --------Join lines-----------------------------------------------
        self.joinline.create(
            vertex=MAX_NAIRCRAFT * 4,
            color=palette.ADSBaircraft,  # type:ignore
            usage=GLBuffer.UsagePattern.StreamDraw,
        )

        # Turn the initialization flag to True
        self.initialized = True

    def draw(self):
        """
        Draw all traffic graphics.
        """

        # Get data for active node
        if self.naircraft == 0 or not self.show_adsb:
            return

        # Send the (possibly) updated global uniforms to the buffer
        self.shaderset.set_vertex_scale_type(  # type:ignore
            self.shaderset.VERTEX_IS_LATLON  # type:ignore
        )
        self.shaderset.enable_wrap(False)  # type:ignore

        self.cpalines.draw()
        self.joinline.draw()

        self.shaderset.enable_wrap(True)  # type:ignore
        # PZ circles only when they are bigger than the A/C symbols
        if self.show_adsb_pz and self.zoom >= 0.15:
            self.shaderset.set_vertex_scale_type(  # type:ignore
                self.shaderset.VERTEX_IS_METERS  # type:ignore
            )
            self.protectedzone.draw(n_instances=self.naircraft)

        # Draw traffic symbols
        self.shaderset.set_vertex_scale_type(  # type:ignore
            self.shaderset.VERTEX_IS_SCREEN  # type:ignore
        )
        self.ac_symbol.draw(n_instances=self.naircraft)

        if self.show_lbl:
            self.aclabels.draw(n_instances=self.naircraft)

    # --------------------------------------------------------------------
    #                      SUBSCIBER FUNCTIONS
    # --------------------------------------------------------------------

    @subscriber(topic="ADSBDATA", actonly=True)
    def update_adsb_data(self, data):
        """
        Update GPU buffers with ADS-B data.
        """

        if not self.initialized:
            return
        if ctx.action == ctx.action.Reset or ctx.action == ctx.action.ActChange:
            # Simulation reset: Clear all entries
            self.naircraft = 0
            self.counter = 0
            return  # Not ready yet

        # Select the current gl surface
        self.glsurface.makeCurrent()
        # Update the number of AC
        self.naircraft = len(data.id)
        self.translvl = data.translvl

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

            # Labels and joining line
            rawlabel = ""
            joinlines = np.zeros(4 * self.naircraft, dtype=np.float32)

            # Adjust the size of saved color arrays
            palette_colors = np.array(
                palette.ADSBaircraft + (255,), dtype=np.uint8
            )  # (4,)

            # Resize current_color to exactly naircraft entries
            if len(self.current_color) != self.naircraft:
                new_colors = np.tile(
                    palette_colors, (self.naircraft, 1)
                )  # (naircraft, 4)
                copy_count = min(len(self.current_color), self.naircraft)
                new_colors[:copy_count] = self.current_color[:copy_count]
                self.current_color = new_colors

            # Loop over all aircraft
            zdata = zip(
                data.id,
                data.callsign,
                data.inconf,
                data.tcpamax,
                data.lat,
                data.lon,
                data.alt,
                data.gs,
                data.vs,
                data.trk,
                data.ss,
                data.gt_lat,
                data.gt_lon,
            )
            for i, (
                acid,
                callsign,
                inconf,
                tcpa,
                lat,
                lon,
                alt,
                gs,
                vs,
                trk,
                ss,
                gt_lat,
                gt_lon,
            ) in enumerate(zdata):

                if i >= MAX_NAIRCRAFT:
                    break

                # First update the label
                if self.show_lbl:
                    if callsign and not np.isnan(alt) and not np.isnan(vs):

                        # First 10 chars: acid, left-justified
                        rawlabel += f"{callsign[:8]:<10}"
                        # Next 10 chars: callsign inside parentheses, truncated/padded to 8 chars total
                        acid_str = f"<{acid[:8]}>"  # 8 chars callsign + 2 for parentheses = 10 chars
                        rawlabel += f"{acid_str:<10}"
                        if alt <= self.translvl:
                            rawlabel += f"{int(alt / ft + 0.5):<10}"
                        else:
                            rawlabel += f"{f'FL{int(alt / ft / 100.0 + 0.5):03d}':<10}"

                        vsarrow = 30 if vs > 0.25 else 31 if vs < -0.25 else 32
                        rawlabel += f"{int(gs / kts + 0.5):<3}{chr(vsarrow):<7}"
                    else:
                        # Fallback row: just <acid> plus 30 spaces
                        rawlabel += f"<{acid[:8]}>".ljust(40)

                if self.show_adsb:
                    # If not in conflict and not in danger, standard colors
                    if ss == 0 or not self.show_danger:
                        rgb = palette.ADSBaircraft  # type:ignore
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
                        # Precompute RGBA tuples once
                        green_rgba = np.array(
                            palette.ADSBaircraft + (255,), dtype=np.uint8
                        )  # type:ignore
                        red_rgba = np.array(
                            palette.ADSBdanger + (255,), dtype=np.uint8
                        )  # type:ignore
                        if self.counter % FLASH_MULT == 0:
                            # Flip between green/red
                            is_green = np.all(
                                self.current_color[i, :3] == palette.ADSBaircraft
                            )  # type:ignore
                            color[i] = red_rgba if is_green else green_rgba

                            # Label always green
                            lblcolor[i] = green_rgba

                            # Update current color
                            self.current_color[i] = color[i]
                        else:
                            color[i] = self.current_color[i]
                            lblcolor[i] = green_rgba

                    # If in conflict, compute CPA lines
                    if inconf:
                        color[i, :] = palette.conflict + (255,)  # type:ignore
                        lblcolor[i, :] = palette.conflict + (255,)  # type:ignore
                        lat1, lon1 = geo.qdrpos(
                            lat,
                            lon,
                            trk,
                            tcpa * gs / nm,
                        )
                        cpalines[4 * confidx : 4 * confidx + 4] = [
                            lat,
                            lon,
                            lat1,
                            lon1,
                        ]
                        confidx += 1
                        self.current_color[i] = color[i]

                    # If both ADS-B and real aircraft are shown, draw join line
                    if self.show_traf and not np.isnan(gt_lat) and not np.isnan(gt_lon):
                        joinlines[4 * i : 4 * i + 4] = [
                            lat,
                            lon,
                            gt_lat,
                            gt_lon,
                        ]

            # Update buffers
            self.lat.update(np.array(data.lat, dtype=np.float32))
            self.lon.update(np.array(data.lon, dtype=np.float32))
            self.hdg.update(np.array(data.trk, dtype=np.float32))
            self.alt.update(np.array(data.alt, dtype=np.float32))
            self.rpz.update(np.array(data.rpz, dtype=np.float32))
            self.joinline.update(vertex=joinlines)
            self.cpalines.update(vertex=cpalines)
            self.lblcolor.update(lblcolor)
            self.color.update(color)
            self.lbl.update(np.array(rawlabel.encode("utf8"), dtype=np.bytes_))

            # Update the counter
            self.counter += 1

    # --------------------------------------------------------------------
    #                      STACK COMMANDS
    # --------------------------------------------------------------------

    @stack.command(name="SHOWDANGER", brief="SHOWDANGER [flag]")
    def showdanger(self, flag: str = ""):
        """Toggle drawing of danger flashes."""

        # Convert string to bool if provided, else keep None
        bool_flag = None if flag == "" else flag.lower() in ("1", "true", "yes", "on")
        self.show_danger = not self.show_danger if bool_flag is None else bool_flag

        return True, f"Show danger flashes {self.show_danger}."

    @stack.command(name="SHOWADSB", aliases=("SHOWADSBTRAF",), brief="SHOWADSB [flag]")
    def showadsbtraf(self, flag: str = ""):
        """Toggle drawing of ADS-B traffic."""

        # Convert string to bool if provided, else keep None
        bool_flag = None if flag == "" else flag.lower() in ("1", "true", "yes", "on")
        self.show_adsb = not self.show_adsb if bool_flag is None else bool_flag

        return True, f"Show ADS-B {self.show_adsb}."

    @stack.command(name="TOGGLEVIEW", brief="TOGGLEVIEW [1/2/3]")
    def toggle_view(self, flag: int):
        """Toggle drawing of aircraft ADS-B traffic."""

        match flag:
            case 1:
                self.show_adsb = False
                self.show_traf = True
                stack.stack("LABEL 2")

                return True, f"TRUE TRAFFIC view."
            case 2:
                self.show_adsb = True
                self.show_traf = False
                stack.stack("LABEL 0")

                return True, f"ADS-B TRAFFIC view."
            case 3:
                self.show_adsb = True
                self.show_traf = True
                stack.stack("LABEL 2")

                return True, f"TRUE and ADS-B TRAFFIC view."
            case _:
                return False, f"{flag} is not a valid value for TOGGLEVIEW."

    @stack.command(name="MGHOST", brief="MGHOST num")
    def mghost(self, num: int):
        """Creates n random GHOST aircraft on current screen."""

        # Pass the call to the stack, with the bound area given by the screen
        stack.forward(
            f'INSIDE {" ".join(str(el) for el in ref.area.bbox)} ATTACK MGHOST {num}'  # type: ignore
        )
        return True

    @stack.command(name="SHOWADSBPZ", brief="SHOWADSBPZ [flag]")
    def showpz(self, flag: str = ""):
        """Toggle drawing of aircraft protected zones."""

        # Convert string to bool if provided, else keep None
        bool_flag = None if flag == "" else flag.lower() in ("1", "true", "yes", "on")
        self.show_adsb_pz = not self.show_adsb_pz if bool_flag is None else bool_flag

        return True, f"Show protected zones {self.show_adsb_pz}."
