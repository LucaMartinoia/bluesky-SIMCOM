import os
import pyModeS as pms
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from bluesky import core, stack, traf
from bluesky.plugins.SIMCOM.tools import id2idx

"""
This module implements two encryption/authentication schemes.
"""


class Security(core.Entity):
    """
    Class that implements cyber-defense mechanisms on ADS-B data.
    """

    def __init__(self) -> None:
        super().__init__()

        # List of implemented schemes
        self.security_str = "AES-GCM, NONE, STATUS, TOGGLE"
        # Module ON/OFF flag
        self.flag = False

        with self.settrafarrays():
            # Scheme type
            self.scheme = []
            self.model = []
            # Keys for the scheme
            self.keyring = []

    def create(self, n: int = 1) -> None:
        """
        Cybersecurity parameters for newly created aircraft.
        """

        super().create(n)

        # Empty fields for newly created aircraft
        self.scheme[-n:] = ["NONE"] * n
        self.model[-n:] = [None] * n
        self.keyring[-n:] = [b""] * n

    # --------------------------------------------------------------------
    #                      SECURITY SCHEMES
    # --------------------------------------------------------------------

    def apply_schemes(self, msg: list[str], counter: int, model) -> tuple[list, int]:
        """
        Wrapper function for the AES-GCM encryption scheme.
        """

        return self.encrypt_AESGCM(msg, counter, model)

    # --------------------------------------------------------------------
    #                      AES-GCM
    # --------------------------------------------------------------------

    def encrypt_AESGCM(self, msg: list[str], counter: int, model) -> tuple[list, int]:
        """
        Model aircraft-side secure ADS-B transmission using AES-GCM.

        The original ADS-B message is split into header (AAD: DF+CA+ICAO) and
        payload; only the payload is encrypted, while the header remains in clear.
        A nonce is constructed from random bytes and a counter, and the GCM tag
        is appended alongside the ciphertext. The CRC is recomputed over the
        modified message, assuming tag and nonce are conveyed as associated
        metadata (e.g., overlay channel). Returns the protected message and the
        incremented counter.
        """

        # Skip empty message
        if not msg or not msg[0]:
            return msg, counter

        # Compute nonce: 8 random bytes + 4-byte counter
        nonce = os.urandom(8) + counter.to_bytes(4, "big")

        # Convert ADS-B message to bytes
        msg_bytes = bytes.fromhex(msg[0])

        # Split header (AAD) and payload
        aad = msg_bytes[:4]  # DF + CA + ICAO
        payload = msg_bytes[4:]  # Remaining bytes

        # Encrypt and authenticate
        ct_full = model.encrypt(nonce, payload, aad)  # type: ignore

        # Split ciphertext and tag (last 16 bytes)
        tag = ct_full[-16:]
        ct = ct_full[:-16]

        # Recompute CRC over AAD + ciphertext
        aad_hex = aad.hex().upper()
        ct_hex = ct.hex().upper()

        msg_for_crc = aad_hex + ct_hex + "000000"
        crc_value = pms.crc(msg_for_crc, encode=True)
        crc_hex = f"{crc_value:06X}"

        full_msg = aad_hex + ct_hex + crc_hex

        # Return encrypted structure
        return [full_msg, tag, nonce], counter + 1

    def AESGCM_check_nonce(self, nonce: bytes, cached_nonce: bytes) -> bool:
        """
        Compare the given nonce with the last stored nonce for aircraft i.
        Returns True if nonce is valid (counter > last), False otherwise.
        """

        # If no cached nonce, accept it
        if not cached_nonce:
            return True

        # Extract counter from 4 last bytes of the nonce
        counter = int.from_bytes(nonce[-4:], "big")
        cached_counter = int.from_bytes(cached_nonce[-4:], "big")

        if counter <= cached_counter:
            return False  # replay or old message

        return True

    def decrypt_AESGCM(
        self, msg: list, cached_nonce: bytes, model
    ) -> tuple[list[str], bytes]:
        """
        Model receiver-side AES-GCM decryption and authentication of a protected
        ADS-B message.

        The header (AAD) is used for authentication while the payload is decrypted
        using the provided nonce and key model. Nonce freshness is enforced to
        mitigate replay; if authentication or nonce validation fails, [""] is
        returned to represent a corrupted/unauthenticated message. On success,
        the original ADS-B hex message is reconstructed and the nonce cache updated.
        """

        if len(msg) != 3:
            return [""], cached_nonce

        nonce = msg[2]

        if not self.AESGCM_check_nonce(nonce, cached_nonce):
            # If old message, drop it and quit
            return [""], cached_nonce
        else:
            # Else update nonce cache
            cached_nonce = nonce

        # Convert hex to bytes
        msg_bytes = bytes.fromhex(msg[0])

        # Split message in aad, tag and payload
        aad = msg_bytes[:4]
        payload = msg_bytes[4:-3]
        crc = msg_bytes[-3:]
        tag = msg[1]  # already in bytes

        # Append stored tag
        ct = payload + tag

        # Decrypt
        try:
            plaintext = model.decrypt(nonce, ct, aad)  # type:ignore
            return [(aad + plaintext + crc).hex().upper()], cached_nonce
        except Exception:
            return [""], cached_nonce

    # --------------------------------------------------------------------
    #                      STACK FUNCTIONS
    # --------------------------------------------------------------------

    @stack.commandgroup(name="SECURITY", brief="SECURITY commands [args]")
    def security(self) -> tuple[bool, str]:
        """
        Cyber-security related commands.
        """

        return True, f"SECURITY command\nPossible subcommands: {self.security_str}."

    @security.subcommand(name="AES-GCM", brief="AES-GCM [acid]")
    def security_AESGCM(self, acid: str = "") -> tuple[bool, str]:
        """
        AES-CGM scheme used by selected aircraft.

        If no ACID is provided, the scheme is applied to all aircraft instead.
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

    @security.subcommand(name="NONE", brief="NONE [acid]")
    def security_none(self, acid: str = "") -> tuple[bool, str]:
        """
        No security scheme used by selected aircraft.

        If no ACID is provided, the scheme is applied to all aircraft instead.
        """

        # Reset fields
        if acid:
            i = id2idx(acid)
            if i == -1:
                return False, "Aircraft does not exists."
            else:
                # For single aircraft
                self.scheme[i] = "NONE"
                self.model[i] = None
                self.keyring[i] = b""

                return True, f"{traf.id[i]} is not using any security schemes."
        else:
            # For all
            n = traf.ntraf

            self.scheme = ["NONE"] * n
            self.model = [None] * n
            self.keyring = [b""] * n

            return True, f"All aircfaft stopped using security schemes."

    @security.subcommand(name="STATUS", brief="STATUS acid")
    def security_status(self, acid: "acid") -> tuple[bool, str]:  # type: ignore
        """
        Show current attack status for a given aircraft.
        """

        return (
            True,
            f"Aircraft {traf.id[acid]} is using {self.scheme[acid]} scheme.",
        )

    @security.subcommand(name="TOGGLE", brief="TOGGLE [flag]")
    def attack_on(self, flag: str = "") -> tuple[bool, str]:
        """
        Enable/disable module.
        """

        if flag == "":
            # No argument: flip current state
            self.flag = not self.flag
        else:
            f = flag.lower()
            if f == "true":
                self.flag = True
            elif f == "false":
                self.flag = False
            else:
                return False, "Flag must be 'true' or 'false'."

        state = "ON" if self.flag else "OFF"
        return True, f"Security module {state}."
