import numpy as np
from bluesky import core, stack, settings, tools, traf
from bluesky.network.publisher import state_publisher
from bluesky.plugins.SIMCOM.conflict_detection import ConflictDetection
from bluesky.plugins.SIMCOM.security import Security
from bluesky.plugins.SIMCOM.datalogger import Logger
from bluesky.plugins.SIMCOM.world import World
from bluesky.plugins.SIMCOM.tools import id2idx

"""
SIMCOM is a BlueSky plugin that extends the core simulator with ADS-B-specific functionality.

It provides a framework to model and analyze aircraft transmissions, receivers,
and potential attacker behavior in a realistic ATM environment. SIMCOM supports
the study of cyber-attacks such as message replay, jamming, spoofing, and ghost
injection, while also enabling testing of security schemes like AES-GCM
encryption and authentication. The plugin integrates propagation, noise, and
receiver selection logic, allowing users to evaluate the impact of attacks and
mitigation strategies on air traffic operations, conflict detection, and overall
system safety.
"""

ACUPDATE_RATE = 2  # Update rate of aircraft update messages [Hz]

settings.set_variable_defaults(log_update=1)


def init_plugin():
    """
    Plugin initialisation function.
    """

    print("\nLoading SIMCOM plugin...")

    # Instantiate singleton entity
    adsbtraffic = Traffic()

    print("SIMCOM plugin fully loaded.\n")

    # Configuration parameters
    config = {
        "plugin_name": "ADSBTRAFFIC",
        "plugin_type": "sim",
        # The update function is called after traffic is updated.
        "update": adsbtraffic.update,
        # "preupdate": adsbtraffic.preupdate,
        # Reset contest
        "reset": adsbtraffic.reset,
    }

    return config

    # --------------------------------------------------------------------
    #                      CORE LOGIC
    # --------------------------------------------------------------------


# Inherit singleton properties from Entity
class Traffic(core.Entity):
    """
    Main SIMCOM class.

    Orchestrate the various entities.
    """

    def __init__(self) -> None:
        super().__init__()

        # Timers for Conflict Detection
        self.asastimer = core.Timer(name="adsb_asas", dt=settings.asas_dt)

        # Data logger
        self.logger = Logger()

        # Global traffic entities
        with self.settrafarrays():
            self.cd = ConflictDetection()
            self.security = Security()
            self.world = World(self.security)

    def create(self, n: int = 1) -> None:
        """
        Default values for newly created aircraft.
        """

        # The childrens are created automatically
        super().create(n)

    def update(self) -> None:
        """
        Perform a full simulation step: propagate ADS-B messages, apply attacker
        logic, and update receiver caches.

        This function orchestrates the world update, triggers ground-based conflict
        detection, and updates ghost/attacker aircraft positions. It represents the
        high-level timestep loop where all message generation, propagation, attack,
        and decoding occur.
        """

        # Update world state
        self.world.update()

        # Ground-based conflict detection update
        if self.asastimer.readynext:
            i_rx = self.world.rx_view
            self.cd.update(self.world.receivers, self.world.receivers, i_rx)

        # Update GHOST position
        self.world.attacker.update()

    def reset(self) -> None:
        """
        Clear all traffic data upon simulation reset.
        """

        # Remove GHOST aircraft
        stack.stack("DELGHOST")

        # This ensures that the traffic arrays (which size is dynamic)
        super().reset()
        self.world.reset()

    # --------------------------------------------------------------------
    #                      PUBLISHER
    # --------------------------------------------------------------------

    @state_publisher(topic="ADSBDATA", dt=1000 // ACUPDATE_RATE)
    def send_ADSB_data(self) -> dict:
        """
        Publish simulation state for visualization, including ADS-B and conflict data.

        The receiver ADS-B In data and conflict detection outputs represent what
        an ATC operator could observe. Other fields (ground-truth positions,
        transmission levels, receiver ranges, etc.) are metadata used for internal
        monitoring, debugging, or non-ATCO views. Each aircraft is identified by
        its unique ID to track individual updates.
        """

        data = dict()

        # Ground-truth data (for joining line)
        data["id"] = traf.id
        data["gt_lon"] = traf.lon
        data["gt_lat"] = traf.lat
        data["translvl"] = traf.translvl

        # Receiver view data
        data["view"] = self.world.view
        data["rxranges"] = self.world.rx_ranges

        # Receiver ADS-B In data
        data["icao"] = self.world.receivers.adsbin.icao
        data["callsign"] = self.world.receivers.adsbin.callsign
        data["ss"] = self.world.receivers.adsbin.ss
        data["lat"] = self.world.receivers.adsbin.lat
        data["lon"] = self.world.receivers.adsbin.lon
        data["alt"] = self.world.receivers.adsbin.alt
        data["gs"] = self.world.receivers.adsbin.gs
        data["vs"] = self.world.receivers.adsbin.vs
        data["trk"] = self.world.receivers.adsbin.trk
        data["attack"] = self.world.receivers.atkflag

        data["spoofing"] = self.world.receivers.spoofing_map

        # Conflict detection data
        data["rpz"] = self.cd.rpz
        data["inconf"] = self.cd.inconf
        data["tcpamax"] = self.cd.tcpamax

        return data

    @core.timed_function(dt=settings.log_update)
    def logging(self) -> None:
        """
        Save data.
        """

        self.logger.logging(
            self.world.aircraft, self.world.attacker, self.world.receivers, self.cd
        )

    @stack.command(name="ADSBPOS", brief="ADSBPOS acid")
    def adsbpos(self, acid: str) -> tuple[bool, str]:  # type:ignore
        """
        Return summary of ADS-B data for a given aircraft.
        """

        i_rx = self.world.rx_view
        i_ac = id2idx(acid)

        if np.isnan(self.world.receivers.adsbin.lat[i_ac][i_rx]):
            lines = "No real data for GHOST aircraft."
        else:
            latlon = tools.misc.latlon2txt(
                self.world.receivers.adsbin.lat[i_ac][i_rx],
                self.world.receivers.adsbin.lon[i_ac][i_rx],
            )
            alt = round(self.world.receivers.adsbin.alt[i_ac][i_rx] / tools.aero.ft)
            trk = round(self.world.receivers.adsbin.trk[i_ac][i_rx])
            gs = round(self.world.receivers.adsbin.gs[i_ac][i_rx] / tools.aero.kts)
            VS = round(
                self.world.receivers.adsbin.vs[i_ac][i_rx] / tools.aero.ft * 60.0
            )
            callsign = self.world.receivers.adsbin.callsign[i_ac][i_rx]

            # Position report
            lines = (
                f"----------------------------------------------\n"
                + f"ADS-B data on {acid} as seen by Receiver RX {i_rx}\n"
                + f"Callsign: {callsign}\n"
                + f"Pos: {latlon}\n"
                + f"Trk: {trk:03d}       Ground speed: {gs} kts\n"
                + f"Alt: {alt} ft        V/S: {VS} fpm\n"
                + "---------------------------------------------\n"
            )

        return True, lines
