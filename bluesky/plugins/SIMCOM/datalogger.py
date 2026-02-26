import pandas as pd
import numpy as np
from datetime import datetime
from bluesky import core, stack, traf, sim

"""
SIMCOM module for logging ADS-B related data.
"""


class Logger(core.Entity):
    """
    Class that implements data loggers.
    """

    def __init__(self) -> None:
        super().__init__()

        # Output folder
        self.log_dir = "output"
        self.fname = "SIMCOM"
        self.flag = False

        # Initialize loggers
        self.reset()

    def reset(self) -> None:
        """
        Reset all the loggers.
        """

        # Reset the loggers and all the vars
        self.conflict_list = []
        self.los_list = []

        # Define the columns
        self.columns = [
            "t",
            "id",
            "lat",
            "lon",
            "alt",
            "gsnorth",
            "gseast",
            "fuelflow",
            "atk_callsign",
            "ghost_lat",
            "ghost_lon",
            "rx_lat",
            "rx_lon",
            "rx_alt",
            "rx_gsnorth",
            "rx_gseast",
            "atk_type",
            "ac_role",
        ]

        # Create an empty DataFrame with the specified columns
        self.df = pd.DataFrame(columns=self.columns)

    def logging(self, aircraft, attacker, receivers, cd) -> None:
        """
        Save data to Dataframe.
        """

        if not self.flag:
            return

        # Conflict detection conflicts and loss of separations
        self.conflict_list.append(cd.confpairs_unique)
        self.los_list.append(cd.lospairs_unique)

        data = {
            "t": sim.simt,
            "id": list(traf.id),
            "lat": traf.lat,
            "lon": traf.lon,
            "alt": traf.alt,
            "gsnorth": traf.gsnorth,
            "gseast": traf.gseast,
            "fuelflow": traf.perf.fuelflow,
            "atk_callsign": attacker.adsbin.callsign,
            "ghost_lat": np.array(attacker.adsbout.lat).flatten(),
            "ghost_lon": np.array(attacker.adsbout.lon).flatten(),
            "rx_lat": np.array(receivers.adsbin.lat).flatten(),
            "rx_lon": np.array(receivers.adsbin.lon).flatten(),
            "rx_alt": np.array(receivers.adsbin.alt).flatten(),
            "rx_gsnorth": np.asarray(receivers.adsbin.gsnorth).flatten(),
            "rx_gseast": np.asarray(receivers.adsbin.gseast).flatten(),
            "atk_type": np.asarray(attacker.type).flatten(),
            "ac_role": np.asarray(aircraft.sharedair.role).flatten(),
        }
        # Convert initial data to a DataFrame
        data_t = pd.DataFrame(data, columns=self.columns)

        self.df = pd.concat([self.df, data_t], ignore_index=True)

    # --------------------------------------------------------------------
    #                      STACK COMMANDS
    # --------------------------------------------------------------------

    @stack.command(name="ADSBLOG", brief="ADSBLOG [fname]")
    def toggle_logging(self, fname: str = "SIMCOM") -> tuple[bool, str]:
        """
        Create the CSV file for the logging.
        """

        # If the flag is False, enable logging
        if not self.flag:
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            fname = f"{fname}_{timestamp}.csv"
            self.fname = f"output/{fname}"

            # Enable logging
            self.flag = True
            return True, f"Saving data to {fname}..."
        else:
            # Save dataframe to CSV
            self.df.to_csv(self.fname, index=False)

            # Stop logging
            self.flag = False
            self.fname = ""
            return True, "Data logging has stopped."
