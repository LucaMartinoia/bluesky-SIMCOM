import numpy as np
from bluesky import (
    core,
    stack,
    sim,
    traf,
    sim,
    ref,
    settings,
)  # , settings, navdb, sim, scr, tools
import pyModeS as pms
from bluesky.tools.aero import ft, Rearth, kts, nm
from random import randint
from bluesky.tools.misc import txt2alt
from bluesky.plugins.SIMCOM.v4.adsb_protocol import (
    ADSB_identification,
    ADSB_position,
    ADSB_velocity,
    ADSB_UPDATE,
)

"""SIMCOM plugin that implements cyber-attacks on the ADS-B protocol."""


def init_plugin():
    """Plugin initialisation function."""

    print("SIMCOM: Loading ADS-B attack plugin...")

    # Instantiate singleton entity
    adsbattacks = ADSBattacks()

    # Configuration parameters
    config = {
        "plugin_name": "ADSBATTACKS2",
        "plugin_type": "sim",
        # The update function is called after traffic is updated.
        "update": adsbattacks.update_ADSBpos,
        # Reset contest
        "reset": adsbattacks.reset,
    }

    return config


class ADSBattacks(core.Entity):
    def __init__(self):
        super().__init__()

        self.attack_str = "FREEZE, HIDE, JUMP, MGHOST, GHOST, NONE, STATUS, DELGHOST"

        # All classes deriving from Entity can register lists and numpy arrays
        # that hold per-aircraft data. This way, their size is automatically
        # updated when aircraft are created or deleted in the simulation.
        with traf.settrafarrays():
            traf.ADSBattack_arg = []

    def create(self, n=1):
        """When new AC are created, they are appended with a new field that stores
        the cyber-attack parameters."""

        super().create(n)

        traf.ADSBattack_arg[-n:] = [{} for _ in range(n)]

    @core.timed_function(
        dt=ADSB_UPDATE, hook="update"
    )  # runs every 0.5 simulated seconds
    def man_in_the_middle(self):
        """This function is called every 0.5s, right after ADSBupdate in protocol.py.
        It overwrites the ADS-B messages depending on the attack before they are sent to the GPU.
        """

        self.mitm_freeze()
        self.mitm_hide()
        self.mitm_jump()
        self.mitm_ghost()

    def reset(self):
        """Clear all traffic data upon simulation reset."""
        # Some child reset functions depend on a correct value of self.ntraf
        self.remove_ghost_aircraft()
        traf.ntraf = 0

        # This ensures that the traffic arrays (which size is dynamic)
        # are all reset as well, so all lat,lon,sdp etc but also objects adsb
        super().reset()

    def update_ADSBpos(self):
        """Update the ADS-B position for ghost AC."""

        mask = traf.ADSBattack == "GHOST"
        if not np.any(mask):
            return  # Nothing to update

        traf.ADSBaltBaro[mask] = np.round(
            traf.ADSBaltBaro[mask] + traf.ADSBvs[mask] * sim.simdt, 6
        )
        traf.ADSBaltGNSS[mask] = traf.ADSBaltBaro[mask]
        traf.ADSBlat[mask] = traf.ADSBlat[mask] + np.degrees(
            sim.simdt * traf.ADSBgsnorth[mask] / Rearth
        )
        coslat = np.cos(np.deg2rad(traf.ADSBlat[mask]))
        traf.ADSBlon[mask] = traf.ADSBlon[mask] + np.degrees(
            sim.simdt * traf.ADSBgseast[mask] / (coslat * Rearth)
        )

    # --------------------------------------------------------------------
    #                      ATTACKS
    # --------------------------------------------------------------------

    def mitm_freeze(self):
        """Simulate jamming by freezing ADS-B outputs to last known values."""

        mask = traf.ADSBattack == "FREEZE"
        indices = np.where(mask)[0]

        for i in indices:
            traf.ADSBmsg_pos_e[i] = traf.ADSBattack_arg[i]["msg_pos_o"]
            traf.ADSBmsg_pos_o[i] = traf.ADSBattack_arg[i]["msg_pos_e"]

    def mitm_hide(self):
        """Simulate jamming by deleting ADS-B outputs."""

        mask = traf.ADSBattack == "HIDE"
        indices = np.where(mask)[0]

        if len(indices) > 0:
            traf.ADSBmsg_pos_e[mask] = None
            traf.ADSBmsg_pos_o[mask] = None

    def mitm_jump(self):
        """Simulate a jummping attack that changes ADS-B position."""

        mask = traf.ADSBattack == "JUMP"
        indices = np.where(mask)[0]

        for i in indices:
            lat, lon = pms.adsb.airborne_position(
                str(traf.ADSBmsg_pos_e[i]),
                str(traf.ADSBmsg_pos_o[i]),
                0,
                1,
            )
            alt = pms.adsb.altitude(str(traf.ADSBmsg_pos_e[i])) * ft

            traf.ADSBlat[i] = lat + traf.ADSBattack_arg[i]["lat"]
            traf.ADSBlon[i] = lon + traf.ADSBattack_arg[i]["lon"]
            traf.ADSBaltBaro[i] = alt + traf.ADSBattack_arg[i]["alt"]

            traf.ADSBmsg_pos_o[i] = ADSB_position(traf.id[i], False)
            traf.ADSBmsg_pos_e[i] = ADSB_position(traf.id[i], True)

    def mitm_ghost(self):
        """Simulate ghost aircraft."""

        mask = traf.ADSBattack == "GHOST"
        indices = np.where(mask)[0]

        for i in indices:
            traf.ADSBmsg_pos_o[i] = ADSB_position(traf.id[i], False)
            traf.ADSBmsg_pos_e[i] = ADSB_position(traf.id[i], True)
            traf.ADSBmsg_v[i] = ADSB_velocity(traf.id[i])
            traf.ADSBmsg_id[i] = ADSB_identification(traf.id[i])

    # --------------------------------------------------------------------
    #                      STACK COMMANDS
    # --------------------------------------------------------------------

    @stack.commandgroup(name="ATTACK", brief="ATTACK commands")
    def attack(self):
        """Cyber-attack related commands."""

        return True, (f"ATTACK command\nPossible subcommands: {self.attack_str}.")

    @attack.subcommand(name="FREEZE", brief="FREEZE acid")
    def attack_freeze(self, acid: "acid"):  # type: ignore
        """FREEZE attack for a given aircraft."""

        traf.ADSBattack[acid] = "FREEZE"
        traf.ADSBattack_arg[acid] = {
            "msg_pos_o": traf.ADSBmsg_pos_o[acid],
            "msg_pos_e": traf.ADSBmsg_pos_e[acid],
        }

        return True, f"{traf.id[acid]} is under FREEZE attack."

    @attack.subcommand(name="HIDE", brief="HIDE acid")
    def attack_hide(self, acid: "acid"):  # type: ignore
        """HIDE attack for a given aircraft."""

        traf.ADSBattack[acid] = "HIDE"
        traf.ADSBattack_arg[acid] = {
            "msg_pos_o": None,
            "msg_pos_e": None,
        }

        return True, f"{traf.id[acid]} is under HIDE attack."

    @attack.subcommand(name="JUMP", brief="JUMP acid,lat-diff,lon-diff,alt-diff")
    def attack_jump(self, acid: "acid", lat: float, lon: float, alt: str):  # type: ignore
        """JUMP attack for a given aircraft."""

        traf.ADSBattack[acid] = "JUMP"
        traf.ADSBattack_arg[acid] = {
            "lat": lat,
            "lon": lon,
            "alt": txt2alt(alt),
        }
        return True, f"{traf.id[acid]} is under JUMP attack."

    @attack.subcommand(name="MGHOST", brief="MGHOST num")
    def attack_multi_ghost(self, num: int):  # type: ignore
        """Creates n random GHOST aircraft."""

        area = ref.area.bbox

        # Generate random callsigns and icao
        idtmp = chr(randint(65, 90)) + chr(randint(65, 90)) + "{:>05}"
        acid = [idtmp.format(i) for i in range(num)]
        icao = [f"{randint(0, 0xFFFFFF):06X}" for i in range(num)]

        # Generate random positions
        aclat = np.random.rand(num) * (area[2] - area[0]) + area[0]
        aclon = np.random.rand(num) * (area[3] - area[1]) + area[1]
        achdg = np.random.randint(1, 360, num)
        acalt = np.random.randint(2000, 39000, num) * ft
        acspeed = np.random.randint(250, 450, num) * kts
        rads = np.deg2rad(achdg)
        gsnorth = acspeed * np.sin(rads)
        gseast = acspeed * np.cos(rads)
        gs = np.sqrt(gsnorth**2 + gseast**2)
        acvs = np.zeros(num)

        # Fix values
        cap = np.full(num, 5, dtype=int)
        emitter = np.full(num, 3, dtype=int)
        time = np.zeros(num, dtype=int)
        status = np.zeros(num, dtype=int)
        antenna = np.ones(num, dtype=int)
        intent = np.zeros(num, dtype=int)
        NACv = np.full(num, 2, dtype=int)

        # Append ghost values to ADS-B fields
        traf.id = traf.id + acid

        traf.ADSBattack = np.concatenate((traf.ADSBattack, np.array(["GHOST"] * num)))
        traf.ADSBlat = np.concatenate((traf.ADSBlat, aclat))
        traf.ADSBlon = np.concatenate((traf.ADSBlon, aclon))
        traf.ADSBhdg = np.concatenate((traf.ADSBhdg, achdg))
        traf.ADSBaltBaro = np.concatenate((traf.ADSBaltBaro, acalt))
        traf.ADSBaltGNSS = np.concatenate((traf.ADSBaltGNSS, acalt))
        traf.ADSBicao = np.concatenate((traf.ADSBicao, np.array(icao)))
        traf.ADSBcallsign = traf.ADSBcallsign + acid
        traf.ADSBgsnorth = np.concatenate((traf.ADSBgsnorth, gsnorth))
        traf.ADSBgseast = np.concatenate((traf.ADSBgseast, gseast))
        traf.ADSBgs = np.concatenate((traf.ADSBgs, gs))
        traf.ADSBvs = np.concatenate((traf.ADSBvs, acvs))

        traf.ADSBcapability = np.concatenate((traf.ADSBcapability, cap))
        traf.ADSBemitter_category = np.concatenate((traf.ADSBemitter_category, emitter))
        traf.ADSBtime_bit = np.concatenate((traf.ADSBtime_bit, time))
        traf.ADSBsurveillance_status = np.concatenate(
            (traf.ADSBsurveillance_status, status)
        )
        traf.ADSBantenna_flag = np.concatenate((traf.ADSBantenna_flag, antenna))
        traf.ADSBintent_change = np.concatenate((traf.ADSBintent_change, intent))
        traf.ADSBNACv = np.concatenate((traf.ADSBNACv, NACv))

        # Temporary arrays to hold new messages
        new_pos_o = np.empty(num, dtype="<U28")
        new_pos_e = np.empty(num, dtype="<U28")
        new_id = np.empty(num, dtype="<U28")
        new_v = np.empty(num, dtype="<U28")

        # Concatenate with the existing arrays
        traf.ADSBmsg_pos_o = np.concatenate((traf.ADSBmsg_pos_o, new_pos_o))
        traf.ADSBmsg_pos_e = np.concatenate((traf.ADSBmsg_pos_e, new_pos_e))
        traf.ADSBmsg_id = np.concatenate((traf.ADSBmsg_id, new_id))
        traf.ADSBmsg_v = np.concatenate((traf.ADSBmsg_v, new_v))
        traf.ADSBattack_arg.extend([{} for _ in range(num)])

        # Conflict detection data: TO CHANGE WHEN MODIFYING THE ALGORITHM
        traf.cd.inconf = np.concatenate((traf.cd.inconf, np.zeros(num, dtype=int)))
        traf.cd.tcpamax = np.concatenate((traf.cd.tcpamax, np.zeros(num, dtype=int)))
        traf.cd.rpz = np.concatenate(
            (traf.cd.rpz, np.full(num, settings.asas_pzr * nm))
        )
        traf.cd.hpz = np.concatenate(
            (traf.cd.hpz, np.full(num, settings.asas_pzh * ft))
        )
        traf.cd.dtlookahead = np.concatenate(
            (traf.cd.dtlookahead, np.full(num, settings.asas_dtlookahead))
        )

        return True, f"Created {num} GHOST aircraft."

    @attack.subcommand(name="GHOST", brief="GHOST acid,lat,lon,hdg,alt,spd")
    def attack_ghost(
        self, callsign: str, lat: float, lon: float, hdg: float, alt: str, spd: float
    ):
        """Creates a GHOST aircraft."""

        ######## MIGHT BE NECESSARY TO ALSO ADD data.inconf, data.tcpamax TO AVOID CONFLICT DETECTION CRASHES

        alt = txt2alt(alt)
        spd = spd * kts
        id = chr(randint(65, 90)) + chr(randint(65, 90)) + "{:>05}"

        traf.ADSBattack = np.append(traf.ADSBattack, "GHOST")
        traf.id.append(id.format(0))
        traf.ADSBicao = np.append(traf.ADSBicao, f"{randint(0, 0xFFFFFF):06X}")

        rads = np.deg2rad(hdg)
        gsnorth = spd * np.sin(rads)
        gseast = spd * np.cos(rads)
        gs = np.sqrt(gsnorth**2 + gseast**2)

        traf.ADSBcallsign.append(callsign)

        traf.ADSBaltBaro = np.append(traf.ADSBaltBaro, alt)
        traf.ADSBaltGNSS = np.append(traf.ADSBaltGNSS, alt)
        traf.ADSBlat = np.append(traf.ADSBlat, lat)
        traf.ADSBlon = np.append(traf.ADSBlon, lon)
        traf.ADSBgsnorth = np.append(traf.ADSBgsnorth, gsnorth)
        traf.ADSBgseast = np.append(traf.ADSBgseast, gseast)
        traf.ADSBvs = np.append(traf.ADSBvs, 0)
        traf.ADSBgs = np.append(traf.ADSBgs, gs)
        traf.ADSBhdg = np.append(traf.ADSBhdg, hdg)

        traf.ADSBcapability = np.append(traf.ADSBcapability, 5)
        traf.ADSBemitter_category = np.append(traf.ADSBemitter_category, 3)
        traf.ADSBtime_bit = np.append(traf.ADSBtime_bit, 0)
        traf.ADSBsurveillance_status = np.append(traf.ADSBsurveillance_status, 0)
        traf.ADSBantenna_flag = np.append(traf.ADSBantenna_flag, 1)
        traf.ADSBintent_change = np.append(traf.ADSBintent_change, 0)
        traf.ADSBNACv = np.append(traf.ADSBNACv, 2)
        traf.ADSBattack_arg.append({})

        traf.ADSBmsg_pos_o = np.append(
            traf.ADSBmsg_pos_o, ADSB_position(traf.id[-1], False)
        )
        traf.ADSBmsg_pos_e = np.append(
            traf.ADSBmsg_pos_e, ADSB_position(traf.id[-1], True)
        )
        traf.ADSBmsg_id = np.append(traf.ADSBmsg_id, ADSB_identification(traf.id[-1]))
        traf.ADSBmsg_v = np.append(traf.ADSBmsg_v, ADSB_velocity(traf.id[-1]))

        # Conflict detection data: TO CHANGE WHEN MODIFYING THE ALGORITHM
        traf.cd.inconf = np.append(traf.cd.inconf, 0)
        traf.cd.tcpamax = np.append(traf.cd.tcpamax, 0)
        traf.cd.rpz = np.append(traf.cd.rpz, settings.asas_pzr * nm)
        traf.cd.hpz = np.append(traf.cd.hpz, settings.asas_pzh * ft)
        traf.cd.dtlookahead = np.append(traf.cd.dtlookahead, settings.asas_dtlookahead)

        return True, f"GHOST aircraft created."

    @attack.subcommand(name="DELGHOST", brief="DELGHOST")
    def remove_ghost_aircraft(self):
        """Remove all ghost aircraft."""

        # Boolean mask: True for aircraft to keep
        keep_mask = np.array(traf.ADSBattack) != "GHOST"

        # Helper to get nested attributes from a dotted path
        def get_nested_attr(obj, attr_path):
            for part in attr_path.split("."):
                obj = getattr(obj, part, None)
                if obj is None:
                    return None
            return obj

        # Helper to set nested attributes from a dotted path
        def set_nested_attr(obj, attr_path, value):
            parts = attr_path.split(".")
            for part in parts[:-1]:
                obj = getattr(obj, part, None)
                if obj is None:
                    return
            setattr(obj, parts[-1], value)

        # Filter a single attribute based on keep_mask
        def filter_attr(name):
            attr = get_nested_attr(traf, name)
            if attr is None:
                return
            try:
                if isinstance(attr, np.ndarray):
                    set_nested_attr(traf, name, attr[keep_mask])
                elif isinstance(attr, list):
                    set_nested_attr(
                        traf, name, [v for v, keep in zip(attr, keep_mask) if keep]
                    )
            except Exception:
                pass  # skip attributes of mismatched length

        # Apply filter to all key traffic fields
        for field in [
            "id",
            "ADSBlat",
            "ADSBlon",
            "ADSBaltBaro",
            "ADSBaltGNSS",
            "ADSBicao",
            "ADSBvs",
            "ADSBhdg",
            "ADSBgsnorth",
            "ADSBgseast",
            "ADSBattack",
            "ADSBgs",
            "ADSBcapability",
            "ADSBemitter_category",
            "ADSBtime_bit",
            "ADSBsurveillance_status",
            "ADSBantenna_flag",
            "ADSBintent_change",
            "ADSBNACv",
            "ADSBmsg_pos_o",
            "ADSBmsg_pos_e",
            "ADSBmsg_id",
            "ADSBmsg_v",
            "ADSBcallsign",
            "cd.inconf",
            "cd.tcpamax",
            "cd.rpz",
            "cd.hpz",
            "cd.dtlookahead",
        ]:
            filter_attr(field)

        # Update counters
        traf.ntraf = len(traf.id)

    @attack.subcommand(name="NONE", brief="NONE acid")
    def attack_none(self, acid: "acid"):  # type: ignore
        """Clear any attack for a given aircraft."""

        if traf.ADSBattack[acid] == "GHOST":
            return False, f"Cannot clear GHOST aircraft. Use ATTACK DELGHOST instead."

        traf.ADSBattack[acid] = "NONE"
        traf.ADSBattack_arg[acid] = {}

        return True, f"{traf.id[acid]} is not under attack."

    @attack.subcommand(name="STATUS", brief="STATUS acid")
    def attack_status(self, acid: "acid"):  # type: ignore
        """Show current attack status for a given aircraft."""

        return (
            True,
            f"Aircraft {traf.id[acid]} is currently under {traf.ADSBattack[acid]} attack.",
        )
