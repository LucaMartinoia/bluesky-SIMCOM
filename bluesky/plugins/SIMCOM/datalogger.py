import pandas as pd
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
            "rx_lat",
            "rx_lon",
            "rx_alt",
        ]

        # Create an empty DataFrame with the specified columns
        self.df = pd.DataFrame(columns=self.columns)

    def logging(self, attacker, receivers, cd) -> None:
        """
        Save data to Dataframe.
        """

        if self.flag:

            # Conflict detection conflicts and loss of separations
            # TODO: need to understand a better way to save CONFPAIRS
            self.conflict_list.append(cd.confpairs_unique)
            self.los_list.append(cd.lospairs_unique)

            data = {
                "t": sim.simt,
                "id": traf.id,
                "lat": traf.lat,
                "lon": traf.lon,
                "alt": traf.alt,
                "gsnorth": traf.gsnorth,
                "gseast": traf.gseast,
                "fuelflow": traf.perf.fuelflow,
                "atk_callsign": attacker.adsbin.callsign,
                "rx_lat": receivers.adsbin.lat,
                "rx_lon": receivers.adsbin.lon,
                "rx_alt": receivers.adsbin.alt,
                "cd": cd.confpairs_unique,
                "los": cd.lospairs_unique,
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
