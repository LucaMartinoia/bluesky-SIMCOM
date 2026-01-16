import os
import pyModeS as pms
from dataclasses import dataclass, fields, field
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from bluesky import core, stack, traf
from bluesky.plugins.SIMCOM.tools import id2idx

"""
This module should implement two encryption/authentication schemes.

One is for AES-CGM. Each aircraft has a key, the same key is owned by the GUI which decode each message.
The other might be TESLA or something.
"""


@dataclass
class Nonces:
    position_even: bytes = field(default_factory=bytes)
    position_odd: bytes = field(default_factory=bytes)
    identification: bytes = field(default_factory=bytes)
    velocity: bytes = field(default_factory=bytes)


class Security(core.Entity):
    """
    Class that implements cyber-defense mechanisms on ADS-B data.
    """

    def __init__(self) -> None:
        """
        Initializing the security class.
        """

        super().__init__()

        self.security_str = "AES-GCM, ON, OFF"  # List of implemented schemes
        self.flag = False  # Module ON/OFF flag

        # Create arrays for the attack arguments and attack type
        with self.settrafarrays():
            self.scheme = []  # Scheme type
            self.model = []
            # Keys for the scheme
            self.keyring = []
            # Nonce counter
            self.counter = []
            # Cached nonces at receiver
            self.nonces = []

    def create(self, n: int = 1) -> None:
        """
        When new aircraft are created, they are appended with a new field that stores
        the security parameters.
        """

        super().create(n)

        # Empty NONE schemes for newly created aircraft
        self.scheme[-n:] = ["NONE"] * n
        self.model[-n:] = [None] * n
        self.counter[-n:] = [1] * n
        self.keyring[-n:] = [b""] * n
        self.nonces[-n:] = [Nonces() for _ in range(n)]

    # --------------------------------------------------------------------
    #                      SECURITY SCHEMES
    # --------------------------------------------------------------------

    def apply_schemes(self, msgs, index: int):
        """
        This function is called in protocol.py.

        msg_type is only used to store values in the appropriate cache.
        In real world this does not exist
        """

        return self.apply_AESGCM(msgs, index)

    # --------------------------------------------------------------------
    #                      AES-GCM
    # --------------------------------------------------------------------

    def apply_AESGCM(self, msgs, index: int):
        """
        Encrypts the message using AES-GCM.
        """

        # Loop over all message types
        for f in fields(msgs):
            msg_type = f.name
            msg = getattr(msgs, msg_type)
            if not msg:
                continue  # skip empty messages

            # Compute the nonce from counter and random
            nonce = os.urandom(8) + self.counter[index].to_bytes(4, "big")

            # Convert ADS-B message in bytes
            msg_bytes = bytes.fromhex(msg[0])

            # Split ADS-B fields in header and payload
            aad = msg_bytes[:4]  # first 4 bytes: DF+CA+ICAO
            payload = msg_bytes[4:]  # all bytes except first 4

            # Encrypt and authenticate
            ct = self.model[index].encrypt(nonce, payload, aad)  # type:ignore

            # Split the cyphertext from the tag (16 bytes)
            tag = ct[-16:]  # Last 16 bytes
            ct = ct[:-16]  # Everything else

            # Compute the new CRC
            ct_hex = ct.hex().upper()
            aad_hex = aad.hex().upper()
            msg_for_crc = (aad_hex + ct_hex) + "000000"
            crc_value = pms.crc(msg_for_crc, encode=True)
            crc_hex = f"{crc_value:06X}"  # 6-digit hex

            # The hex-message
            full_msg = aad_hex + ct_hex + crc_hex

            # Move counter
            self.counter[index] += 1

            # Update the message storing msg, tag and nonce
            setattr(msgs, msg_type, [full_msg, tag, nonce])

        return msgs

    def AESGCM_check_nonce(self, nonce, cached_nonce) -> bool:
        """
        Compare the given nonce with the last stored nonce for aircraft i.
        Returns True if nonce is valid (counter > last), False otherwise.
        """

        # Extract counter from 4 last bytes of the nonce
        counter = int.from_bytes(nonce[-4:], "big")
        cached_counter = int.from_bytes(cached_nonce[-4:], "big") if cached_nonce else 0

        if counter <= cached_counter:
            return False  # replay or old message

        return True

    def decrypt_AESGCM_message(self, msg: list, i: int, msg_type: str):
        """
        Decrypts a single ADS-B hex message using AES-GCM.
        Returns decrypted payload as bytes, or None if CRC/authentication fails.
        """

        if len(msg) != 3:
            return [""]

        cached_nonce = getattr(self.nonces[i], msg_type)
        nonce = msg[2]

        # TODO: Because I am iterating over single messages, I can cache only last nonce
        if not self.AESGCM_check_nonce(nonce, cached_nonce):
            # If old message, drop it and quit
            return [""]
        else:
            # Else update nonce cache
            setattr(self.nonces[i], msg_type, nonce)

        # Convert hex to bytes
        msg_bytes = bytes.fromhex(msg[0])

        # Split message in aad, tag and payload
        aad = msg_bytes[:4]
        payload = msg_bytes[4:-3]
        crc = msg_bytes[-3:]
        tag = msg[1]  # Already in bytes

        # Append stored tag
        ct = payload + tag

        # Decrypt
        try:
            plaintext = self.model[i].decrypt(nonce, ct, aad)  # type:ignore
            return [(aad + plaintext + crc).hex().upper()]
        except Exception:
            return [""]

    # --------------------------------------------------------------------
    #                      STACK FUNCTIONS
    # --------------------------------------------------------------------

    @stack.commandgroup(name="SECURITY", brief="SECURITY commands [args]")
    def security(self):
        """
        Cyber-security related commands.
        """

        return True, f"SECURITY command\nPossible subcommands: {self.security_str}."

    @security.subcommand(name="AES-GCM", brief="AES-GCM [acid]")
    def security_AESGCM(self, acid: str = ""):
        """
        AES-CGM scheme used by selected aircraft.
        """

        if acid != "":
            i = id2idx(acid)
            if i == -1:
                return False, "Aircraft does not exists."
            else:
                self.scheme[i] = "AES-GCM"  # type:ignore
                self.keyring[i] = AESGCM.generate_key(bit_length=128)  # type:ignore
                self.model[i] = AESGCM(self.keyring[i])  # type:ignore

                return True, f"{traf.id[i]} is using AES-GCM scheme."  # type:ignore
        else:
            self.scheme = ["AES-GCM"] * traf.ntraf
            for i in range(traf.ntraf):
                self.keyring[i] = AESGCM.generate_key(bit_length=128)  # type:ignore
                self.model[i] = AESGCM(self.keyring[i])  # type:ignore

            return True, f"All aircraft are using AES-GCM scheme."

    @security.subcommand(name="NONE", brief="NONE acid")
    def security_none(self, acid: "acid"):  # type: ignore
        """
        No security scheme used by selected aircraft.
        """

        # Reset fields
        if acid:
            self.scheme[acid] = "NONE"
            self.model[acid] = None
            self.data[acid] = {}
            self.keyring[acid] = b""
            self.counter[acid] = 0
        else:
            n = traf.ntraf

            self.scheme = ["NONE"] * n
            self.model = [None] * n
            self.data = [{} for _ in range(n)]
            self.keyring = [b""] * n
            self.counter = [0] * n

        return True, f"{traf.id[acid]} is not using any security schemes."

    @security.subcommand(name="STATUS", brief="STATUS acid")
    def security_status(self, acid: "acid"):  # type: ignore
        """
        Show current attack status for a given aircraft.
        """

        return (
            True,
            f"Aircraft {traf.id[acid]} is using {self.scheme[acid]} scheme.",
        )

    @security.subcommand(name="OFF", brief="OFF")
    def security_off(self):
        """
        Disable the security module.
        """

        self.flag = False
        return True, "SECURITY module is OFF."

    @security.subcommand(name="ON", brief="ON")
    def security_on(self):
        """
        Enable the security module.
        """

        self.flag = True
        return True, "SECURITY module is ON."
