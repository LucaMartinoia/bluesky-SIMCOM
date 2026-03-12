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
from bluesky.network.common import ActionType
from bluesky.ui.polytools import PolygonSet
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
        "plugin_name": "ADSBGUI",
        # The type of this plugin.
        "plugin_type": "gui",
    }
    # Start the new visual object
    addvisual("ADSBVIEW")  # Turn on the new overlay
    addvisual("ADSBPOLY")
    stack.stack("TOGGLEVIEW 3")  # Turn on ADS-B + traffic view
    stack.stack("LOADLOC")

    return config


# Static defines
MAX_NAIRCRAFT = 1000
MAX_NCONFLICTS = 2500
FLASH_MULT = 2  # The multiplier for the danger flashes

palette.set_default_colours(
    ADSBaircraft=(0, 255, 0),  # green: normal
    ADSBconflict=(255, 170, 0),  # amber: caution
    ADSBdanger=(255, 0, 0),  # red: immediate danger
    ADSBspoofing=(255, 255, 0),  # yellow: suspicious / untrusted
    ADSBattack=(255, 0, 255),  # magenta: hostile / cyber
    ATKpoly=(255, 0, 0),  # Red attackers
    RXpoly=(0, 255, 150),  # Green receivers
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
        # Receiver view
        self.view = 0

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
        # Select point of view
        self.view = data.view - 1 if data.rxranges else 0

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

            # Current view data lists for update
            lat_update = []
            lon_update = []
            trk_update = []
            alt_update = []

            # Loop over all aircraft
            zdata = zip(
                data.id,
                data.inconf,
                data.tcpamax,
                data.gt_lat,
                data.gt_lon,
            )

            for i, (
                acid,
                inconf,
                tcpa,
                gt_lat,
                gt_lon,
            ) in enumerate(zdata):

                callsign = data.callsign[i][self.view]
                lat = data.lat[i][self.view]
                lon = data.lon[i][self.view]
                alt = data.alt[i][self.view]
                gs = data.gs[i][self.view]
                vs = data.vs[i][self.view]
                trk = data.trk[i][self.view]
                ss = data.ss[i][self.view]
                attackflag = data.attack[i][self.view]

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
                        rawlabel += f"{f'<{acid[:8]}>':<10}" + " " * 30

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
                        # Overwrite default colors
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

                    # TODO: add same-ICAO color map.

                    if attackflag:
                        # Attack color
                        rgba = palette.ADSBattack + (255,)  # type: ignore
                        color[i, :] = rgba
                        lblcolor[i, :] = rgba
                        self.current_color[i] = color[i]

                lat_update.append(lat)
                lon_update.append(lon)
                alt_update.append(alt)
                trk_update.append(trk)

            # Update buffers
            self.lat.update(np.array(lat_update, dtype=np.float32))
            self.lon.update(np.array(lon_update, dtype=np.float32))
            self.hdg.update(np.array(trk_update, dtype=np.float32))
            self.alt.update(np.array(alt_update, dtype=np.float32))
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


# -----------------------------------------------
# -----------------------------------------------
#             AREAS
# -----------------------------------------------
# -----------------------------------------------

# Static defines
POLY_SIZE = 20000  # Max total number of vertices when summing all polygon vertices


class ADSBPoly(RenderObject, layer=-10):
    """
    Poly OpenGL object.
    """

    # Per remote node attributes
    show_poly: ActData[int] = ActData(1)
    polys: ActData[dict] = ActData(group="poly")
    bufdata: ActData[dict] = ActData()

    @stack.command
    def showloc(self, flag: int | None = None):
        """
        Toggle drawing of polygon shapes between off, outline, and outline+fill.
        """

        # Cycle aircraft label through detail level 0,1,2
        if flag is None:
            self.show_poly = (self.show_poly + 1) % 3

        # Or use the argument if it is an integer
        else:
            self.show_poly = min(2, max(0, flag))

    def __init__(self, parent=None):
        super().__init__(parent)

        # Fixed polygons
        self.allpolys = VertexArrayObject(gl.GL_LINES)
        self.allpfill = VertexArrayObject(gl.GL_TRIANGLES)

        # Poly labels
        self.npoly = 0
        self.lbl = GLBuffer()
        self.lat = GLBuffer()
        self.lon = GLBuffer()
        self.lblcolor = GLBuffer()
        self.polylabels = Text(settings.text_size + 2, (5, 1))

        self.prevmousepos = (0, 0)

    def create(self):

        self.allpolys.create(vertex=POLY_SIZE * 16, color=POLY_SIZE * 8)
        self.allpfill.create(vertex=POLY_SIZE * 24, color=np.append(palette.polys, 50))

        self.lat.create(1000, GLBuffer.UsagePattern.StreamDraw)
        self.lon.create(1000, GLBuffer.UsagePattern.StreamDraw)
        self.lbl.create(1000, GLBuffer.UsagePattern.StreamDraw)
        self.lblcolor.create(1000, GLBuffer.UsagePattern.StreamDraw)

        # Poly labels
        self.polylabels.create(
            self.lbl,
            self.lat,
            self.lon,
            self.lblcolor,
            (0, 0),
            instanced=True,
        )

    def draw(self):

        # Send the (possibly) updated global uniforms to the buffer
        self.shaderset.set_vertex_scale_type(self.shaderset.VERTEX_IS_LATLON)

        # --- DRAW THE MAP AND COASTLINES ---------------------------------------------
        # Map and coastlines: don't wrap around in the shader
        self.shaderset.enable_wrap(False)

        # --- DRAW CUSTOM SHAPES (WHEN AVAILABLE) -----------------------------
        if self.show_poly > 0:
            self.allpolys.draw()
            if self.show_poly > 1:
                self.allpfill.draw()

            self.shaderset.set_vertex_scale_type(  # type:ignore
                self.shaderset.VERTEX_IS_SCREEN  # type:ignore
            )
            self.polylabels.draw(n_instances=self.npoly)

    @subscriber(topic="ADSBPOLY")
    def update_poly_data(self, data):

        if ctx.action in (ActionType.Reset, ActionType.ActChange):
            # Simulation reset: Clear all entries
            self.bufdata.clear()
            self.allpolys.set_vertex_count(0)
            self.allpfill.set_vertex_count(0)
            names = data.polys.keys()

        # The data argument passed to this subscriber contains all poly
        # data. We only need the current updates, which are in ctx.action_content
        elif ctx.action == ActionType.Delete:
            # Delete action contains a list of poly names to delete
            names = ctx.action_content["polys"]
        else:
            # All other updates contain a dict with names as keys
            # and updated/new polys as items
            names = ctx.action_content["polys"].keys()

        # Label data
        self.npoly = len(names)
        lblcolor = np.empty((min(self.npoly, 1000), 4), dtype=np.uint8)
        rawlabel = ""
        lat_update = []
        lon_update = []

        # We're either updating a polygon, or deleting it.
        for i, name in enumerate(names):
            # Always delete the old processed data
            self.bufdata.pop(name, None)

            if ctx.action != ctx.action.Delete:
                polydata = data.polys[name]
                try:
                    shape = polydata["shape"]
                    coordinates = polydata["coordinates"]
                    color = polydata.get("color", palette.polys)
                    self.bufdata[name] = self.genbuffers(shape, coordinates, color)
                except:
                    print("Could not process incoming poly data")

                # Labels
                rawlabel += f"{name:<5}"
                lat_update.append(polydata["clat"])
                lon_update.append(polydata["clon"])
                lblcolor[i] = np.array(tuple(color) + (255,), dtype=np.uint8)

        # Update labels
        self.lat.update(np.array(lat_update, dtype=np.float32))
        self.lon.update(np.array(lon_update, dtype=np.float32))
        self.lblcolor.update(lblcolor)
        self.lbl.update(np.array(rawlabel.encode("utf8"), dtype=np.bytes_))

        if self.bufdata:
            self.glsurface.makeCurrent()
            contours, fills, colors = zip(*self.bufdata.values())
            # Create contour buffer with color
            self.allpolys.update(
                vertex=np.concatenate(contours), color=np.concatenate(colors)
            )

            # Create fill buffer
            self.allpfill.update(vertex=np.concatenate(fills))
        else:
            self.allpolys.set_vertex_count(0)
            self.allpfill.set_vertex_count(0)

    @staticmethod
    def genbuffers(shape, coordinates, color=None):
        """
        Generate outline, fill, and colour buffers for given shape.
        """

        # Break up polyline list of (lat,lon)s into separate line segments
        if shape == "LINE" or shape[:4] == "POLY":
            # Input data is list or array: [lat0,lon0,lat1,lon1,lat2,lon2,lat3,lon3,..]
            newdata = np.array(coordinates, dtype=np.float32)

        elif shape == "BOX":
            # Convert box coordinates into polyline list
            # BOX: 0 = lat0, 1 = lon0, 2 = lat1, 3 = lon1 , use bounding box
            newdata = np.array(
                [
                    coordinates[0],
                    coordinates[1],
                    coordinates[0],
                    coordinates[3],
                    coordinates[2],
                    coordinates[3],
                    coordinates[2],
                    coordinates[1],
                ],
                dtype=np.float32,
            )

        elif shape == "CIRCLE":
            # Input data is latctr,lonctr,radius[nm]
            # Convert circle into polyline list

            # Circle parameters
            Rearth = 6371000.0  # radius of the Earth [m]
            numPoints = 72  # number of straight line segments that make up the circrle

            # Inputs
            lat0 = coordinates[0]  # latitude of the center of the circle [deg]
            lon0 = coordinates[1]  # longitude of the center of the circle [deg]
            Rcircle = coordinates[2] * 1852.0  # radius of circle [NM]

            # Compute flat Earth correction at the center of the experiment circle
            coslatinv = 1.0 / np.cos(np.deg2rad(lat0))

            # compute the x and y coordinates of the circle
            angles = np.linspace(0.0, 2.0 * np.pi, numPoints)  # ,endpoint=True) # [rad]

            # Calculate the circle coordinates in lat/lon degrees.
            # Use flat-earth approximation to convert from cartesian to lat/lon.
            latCircle = lat0 + np.rad2deg(Rcircle * np.sin(angles) / Rearth)  # [deg]
            lonCircle = lon0 + np.rad2deg(
                Rcircle * np.cos(angles) * coslatinv / Rearth
            )  # [deg]

            # make the data array in the format needed to plot circle
            newdata = np.empty(2 * numPoints, dtype=np.float32)  # Create empty array
            newdata[0::2] = latCircle  # Fill array lat0,lon0,lat1,lon1....
            newdata[1::2] = lonCircle

        # Create polygon contour buffer
        # Distinguish between an open and a closed contour.
        # If this is a closed contour, add the first vertex again at the end
        # and add a fill shape
        if shape[-4:] == "LINE":
            contourbuf = np.empty(2 * len(newdata) - 4, dtype=np.float32)
            contourbuf[0::4] = newdata[0:-2:2]  # lat
            contourbuf[1::4] = newdata[1:-2:2]  # lon
            contourbuf[2::4] = newdata[2::2]  # lat
            contourbuf[3::4] = newdata[3::2]  # lon
            fillbuf = np.array([], dtype=np.float32)
        else:
            contourbuf = np.empty(2 * len(newdata), dtype=np.float32)
            contourbuf[0::4] = newdata[0::2]  # lat
            contourbuf[1::4] = newdata[1::2]  # lon
            contourbuf[2:-2:4] = newdata[2::2]  # lat
            contourbuf[3:-3:4] = newdata[3::2]  # lon
            contourbuf[-2:] = newdata[0:2]
            pset = PolygonSet()
            pset.addContour(newdata)
            fillbuf = np.array(pset.vbuf, dtype=np.float32)

        # Define color buffer for outline
        defclr = tuple(color or palette.polys) + (255,)
        colorbuf = np.array(len(contourbuf) // 2 * defclr, dtype=np.uint8)

        # Store new or updated polygon by name, and concatenated with the
        # other polys
        return contourbuf, fillbuf, colorbuf
