import pandas as pd
from bluesky import (
    core,
    stack,
    traf,
    sim,
    settings,
)  # , settings, navdb, sim, scr, tools

"""
SIMCOM module for logging ADS-B related data.

TODO:
- create new functions to create dataframes from various type of data (ground-truth, ADS-B, conflict detection).
- create flags so that the timed-function updates only if flags are True.
- create new stack commands to save various kind of dataframes to CSV.
"""

LOG_UPDATE = 1  # Update dt for LOG [s]


class ADSBlog(core.Entity):
    """
    Class that implements data loggers.
    """

    def __init__(self):
        """
        Initializing the attack class.
        """

        super().__init__()
        # Create the loggers
        self.conflict_list = []
        self.los_list = []
        self.log_dir = "output"
        # Define the columns
        self.columns = ["id", "lat", "lon"]

        # Create an empty DataFrame with the specified columns
        self.df = pd.DataFrame(columns=self.columns)

    def reset(self):
        # Reset the loggers and all the vars
        self.conflict_list = []
        self.los_list = []

        # Define the columns
        self.columns = ["id", "lat", "lon"]

        # Create an empty DataFrame with the specified columns
        self.df = pd.DataFrame(columns=self.columns)

    # @core.timed_function(name="pos_tracking", dt=LOG_UPDATE)
    def pos_tracking(self):
        # Initial positions and parameters
        data = {
            "id": traf.id,
            "lat": traf.lat,
            "lon": traf.lon,
        }

        # Convert initial data to a DataFrame
        data_t = pd.DataFrame(data, columns=self.columns)

        self.df = pd.concat([self.df, data_t], ignore_index=True)

    # @stack.command(name="LogPos")
    def data_logger(self, scenario_name):
        current_datetime = datetime.datetime.now()
        formatted_datetime = current_datetime.strftime("%Y_%m_%d_%H_%M_%S")

        self.df.to_csv(
            f"{self.log_dir}/pos_evolve_{scenario_name}_{formatted_datetime}.log"
        )

        return
