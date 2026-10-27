"""Serial communication interface for PFAFF sewing machines."""

import re
import time
import serial
import serial.tools.list_ports
from PyQt5.QtCore import QCoreApplication
from model import (
    StitchPattern, DEFAULT_PALETTE, elem_has_coords, ELEM_COLOR, ELEM_STITCH,
)
from app_logger import AppLogger
from machine_models import (
    PMEMORY_VARIANT_1475CD,
    find_model,
    find_model_by_ident,
)


def _tr(s):
    """Translate a string in the MachineComm context."""
    return QCoreApplication.translate("MachineComm", s)


def _u16be(value):
    """Return *value* as an unsigned big-endian 16-bit byte pair."""
    return (value & 0xFFFF).to_bytes(2, "big")


def _u16le(value):
    """Return *value* as an unsigned little-endian 16-bit byte pair."""
    return (value & 0xFFFF).to_bytes(2, "little")


def _s8(value):
    """Interpret *value* as a signed 8-bit number."""
    return value - 256 if value >= 128 else value


def _u16be_at(data, offset):
    """Read a big-endian unsigned 16-bit value from *data* at *offset*."""
    return int.from_bytes(data[offset:offset + 2], "big")


class MachineCommError(Exception):
    """Raised when communication with the machine fails or gives an unexpected response."""


class _LoggedSerial:
    """Wraps a ``serial.Serial`` to transparently log all reads and writes."""

    def __init__(self, serial_port, logger):
        self._serial = serial_port
        self._logger = logger

    def write(self, data):
        self._logger.log_send(bytes(data))
        return self._serial.write(data)

    def read(self, size=1):
        data = self._serial.read(size)
        if data:
            self._logger.log_receive(bytes(data))
        return data

    def read_until(self, *args, **kwargs):
        data = self._serial.read_until(*args, **kwargs)
        if data:
            self._logger.log_receive(bytes(data))
        return data

    def __getattr__(self, name):
        return getattr(self._serial, name)

    def __setattr__(self, name, value):
        if name in ("_serial", "_logger"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._serial, name, value)


class MachineComm:
    """Handles serial communication with a sewing machine.

    Default connection parameters: 4800 baud, 8 data bits, no parity, 1 stop bit.
    """

    DEFAULT_BAUDRATE = 4800
    FAST_BAUDRATE = 10472
    DEFAULT_BYTESIZE = serial.EIGHTBITS
    DEFAULT_PARITY = serial.PARITY_NONE
    DEFAULT_STOPBITS = serial.STOPBITS_ONE

    CTRL_ETX = 0x03 # End of Text
    CTRL_EOT = 0x04 # End of Transmission
    CTRL_ENQ = 0x05 # Enquiry
    CTRL_ACK = 0x06 # Acknowledge
    CTRL_BEL = 0x07 # Bell
    CTRL_BS  = 0x08 # Backspace
    CTRL_NAK = 0x15 # Negative Acknowledge
    CTRL_ETB = 0x17 # End of Transmission Block

    # Card-slot preview bitmaps, keyed by the protocol's pattern-type name:
    # (height, width) in pixels.  Columns are stored bottom-to-top in
    # ``height // 8`` bytes each, so one block occupies ``width * height / 8``
    # bytes.
    PREVIEW_SIZES = {
        "9mm": (24, 53),
        "MAXI": (48, 53),
        "Embroidery": (48, 48),
    }
    # 48 * 48 / 8 = 288 bytes per embroidery preview block.
    EMBROIDERY_PREVIEW_SIZE = (
        PREVIEW_SIZES["Embroidery"][0] * PREVIEW_SIZES["Embroidery"][1] // 8
    )

    # A card slot holds roughly 32 kB of raw payload (PCD splits writes at
    # ~32500 B; the exact limit is unknown).  The cap covers the whole KN
    # transfer - filename, preview block(s) and stitch stream - not just the
    # stitch stream, because the machine stores all of it in the slot.
    CARD_MAX_PAYLOAD = 32500

    # Embroidery (hoop) card encoding.  Canvas units are 1/6 mm, payload units
    # are ~1/10 mm; the conversion is a truncating scale, never a rounding.
    EMBROIDERY_SCALE = 256 / 153
    EMBROIDERY_HOOPS = {
        # stitch_type: (header hoop byte, width, height) in payload units
        "small hoop": (1, 803, 803),
        "large hoop": (2, 1204, 1154),
    }

    # Embroidery payload opcodes.  A 0x80 byte opens a command group that runs
    # to the next 0x80; any other byte pair is a plain relative step.  The writer
    # emits these as literals (see _build_embroidery_payload); the reader names
    # them so the walk stays readable.
    EMBROIDERY_GROUP_MARK = 0x80
    EMBROIDERY_CMD_ABS_POS = 0x2E
    EMBROIDERY_CMD_REL_JUMP = 0x23
    EMBROIDERY_CMD_TITLE = 0x27
    EMBROIDERY_CMD_EXTRA = 0x28
    EMBROIDERY_CMD_EXTRA16 = (0x2A, 0x2B)
    EMBROIDERY_CMD_END = 0x2F
    EMBROIDERY_CMD_COLOUR_MIN = 0x30
    EMBROIDERY_CMD_COLOUR_MAX = 0x3F
    # Sub-types of the 0x28 "extra data" command, selected by its first byte.
    EMBROIDERY_EXTRA_ORIGIN = 0x00
    EMBROIDERY_EXTRA_COLOUR_TABLE = 0x01
    # Groups that carry neither a stitch nor data we keep: jump markers, applique
    # stops, speed limits and the undocumented ones.
    EMBROIDERY_IGNORED_COMMANDS = (
        frozenset({0x20, 0x21, 0x24, 0x25, 0x26, 0x29, 0x2C, 0x2D})
        | frozenset(range(0x01, 0x0F))
        | frozenset(range(0x40, 0x44))
    )

    # Default thread palette for card patterns whose payload names colour numbers
    # but carries no colour table. Defined once in the model, because a new
    # embroidery design starts from the same colours.
    DEFAULT_CARD_COLORS = DEFAULT_PALETTE

    def __init__(self, logger=None):
        self._serial = None
        # Logger for traffic/event lines, injected by the app.  A deactivated
        # AppLogger is used when none is supplied so logging calls stay safe.
        self._logger = logger if logger is not None else AppLogger(enabled=False)

    # ── Port enumeration ──

    @staticmethod
    def list_ports():
        """Return a list of available serial ports on the system.

        Returns:
            list[str]: Sorted list of port names (e.g. ['COM1', 'COM3']).
        """
        ports = serial.tools.list_ports.comports()
        return sorted(p.device for p in ports)

    # ── Connection management ──

    def open(
        self,
        port,
        baudrate=DEFAULT_BAUDRATE,
        bytesize=DEFAULT_BYTESIZE,
        parity=DEFAULT_PARITY,
        stopbits=DEFAULT_STOPBITS,
        timeout=None,
    ):
        """Open a serial connection to the sewing machine.

        Args:
            port (str): Serial port name (e.g. 'COM3' or '/dev/ttyUSB0').
            baudrate (int): Baud rate. Default: 4800.
            bytesize: Number of data bits. Default: serial.EIGHTBITS (8).
            parity: Parity checking. Default: serial.PARITY_NONE.
            stopbits: Number of stop bits. Default: serial.STOPBITS_ONE.
            timeout (float | None): Read timeout in seconds. None = blocking.

        Raises:
            serial.SerialException: If the port cannot be opened.
        """
        if self._serial and self._serial.is_open:
            self._serial.close()

        self._serial = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=bytesize,
            parity=parity,
            stopbits=stopbits,
            timeout=timeout,
        )

        if self._logger.enabled:
            self._serial = _LoggedSerial(self._serial, self._logger)

    def close(self):
        """Close the serial connection if open."""
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None

    @property
    def is_open(self):
        """Return True if the serial port is currently open."""
        return self._serial is not None and self._serial.is_open

    # ── Data transfer ──

    def send(self, data):
        """Send a raw byte array to the machine.

        Args:
            data (bytes | bytearray): Data to transmit.

        Raises:
            serial.SerialException: If the port is not open or a write error occurs.
        """
        self._require_open()
        self._serial.write(data)

    def read(self, size=None):
        """Read bytes from the receive buffer.

        Args:
            size (int | None): Number of bytes to read. If None, reads all
                bytes currently available in the input buffer.

        Returns:
            bytes: Data read from the port.

        Raises:
            serial.SerialException: If the port is not open or a read error occurs.
        """
        self._require_open()
        if size is None:
            size = max(1, self._serial.in_waiting)
        return self._serial.read(size)

    def read_until(self, terminator):
        """Read bytes until a specific terminator character is received.

        Args:
            terminator (bytes | int): Terminator byte(s) to stop at. If an int
                is provided it is converted to a single-byte bytes object.

        Returns:
            bytes: All data read, including the terminator.

        Raises:
            serial.SerialException: If the port is not open or a read error occurs.
        """
        self._require_open()
        if isinstance(terminator, int):
            terminator = bytes([terminator])
        return self._serial.read_until(terminator)

    def flush(self):
        """Flush both the input (read) and output (write) buffers.

        Raises:
            serial.SerialException: If the port is not open.
        """
        self._require_open()
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()

    def flush_read(self):
        """Flush (discard) the input (receive) buffer.

        Raises:
            serial.SerialException: If the port is not open.
        """
        self._require_open()
        self._serial.reset_input_buffer()

    def flush_write(self):
        """Flush (wait until all data is sent) the output (transmit) buffer.

        Raises:
            serial.SerialException: If the port is not open.
        """
        self._require_open()
        self._serial.reset_output_buffer()

    # ── Machine identification ──

    # Pattern: "...PFAFF AG Creative 7570B    Vers. 2.1"
    _IDENT_PATTERN = re.compile(
        r'PFAFF\s+AG\s+(.+?)\s+Vers\.\s+([\d.]+)',
        re.IGNORECASE,
    )

    def query_machine(self, retries=15, retry_delay=0.05, timeout=1.0):
        """Query the machine for its identification string.

        Sends CTRL_BEL repeatedly until the machine responds. The machine
        replies with a text such as:
            "Copyright 1992 - 97       G.M. PFAFF AG Creative 7570B    Vers. 2.1"
        terminated with CTRL_END_OF_TEXT (0x03).
        If no response is received after all retries, CTRL_END_OF_TRANSMISSION
        is sent to signal the machine to abort, then MachineCommError is raised.

        Args:
            retries (int): Maximum number of CTRL_BEL attempts. Default: 15.
            retry_delay (float): Seconds to wait between retries. Default: 0.05.
            timeout (float): Read timeout in seconds per attempt. Default: 1.0.

        Returns:
            dict: {
                'raw':          str  - full response text (terminator stripped),
                'machine_type': str  - e.g. "Creative 7570B",
                'version':      str  - e.g. "2.1",
            }

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: If no valid response is received after all retries,
                or the response does not match the expected format.
        """
        self._require_open()

        self._logger.log_info("query_machine()")

        saved_timeout = self._serial.timeout
        self._serial.timeout = timeout

        try:
            self.flush()
            for attempt in range(retries):
                self._serial.write(bytes([self.CTRL_BEL]))

                # Check how many bytes are available to read after the response delay
                time.sleep(retry_delay)  # Wait briefly for the machine to respond

                if self._serial.in_waiting == 0:
                    continue  # No response, try again
                else:
                    break

            raw = self._serial.read_until(expected=bytes([self.CTRL_ETX]))

            if raw and raw[-1:] == bytes([self.CTRL_ETX]):
                # Strip the terminator and decode
                text = raw[:-1].decode('ascii', errors='replace').strip()

                if not text.startswith("Copyright"):
                    self._logger.log_error(f"Unexpected identification response: {text!r}")
                    raise MachineCommError(
                        _tr("Unexpected identification response: {0}").format(repr(text))
                    )

                canonical = find_model_by_ident(text)
                if canonical is None:
                    self._logger.log_error(f"Unrecognised machine model in response: {text!r}")
                    raise MachineCommError(
                        _tr("Unrecognised machine model in response: {0}").format(repr(text))
                    )

                match = self._IDENT_PATTERN.search(text)
                return {
                    'raw':          text,
                    'machine_type': match.group(1).strip() if match else '',
                    'version':      match.group(2).strip() if match else '',
                    'model':        canonical.name,
                }

            # No response after all retries — signal the machine to abort
            self._serial.write(bytes([self.CTRL_EOT]))
            self._logger.log_error("Machine not responding after all retries")
            raise MachineCommError(
                # f"No response from machine after {retries} attempt(s)."
                _tr("Machine not responding. Please check connection and try again.")
            )
        finally:
            self._serial.timeout = saved_timeout

    # ── Checksum utilities ──

    @staticmethod
    def checksum(data):
        """Calculate checksum for data by summing all bytes modulo 256.

        Args:
            data (bytes | bytearray): Data to calculate checksum for.

        Returns:
            int: Checksum value (0-255).
        """
        checksum_val = 0
        for byte in data:
            checksum_val = (checksum_val + byte) & 0xFF
        return checksum_val

    # ── Transmission control ──

    def end_transmission(self):
        """Send CTRL_EOT to signal end of session and close the serial port."""
        if self._serial and self._serial.is_open:
            try:
                self._serial.write(bytes([self.CTRL_EOT]))
            except Exception:
                pass
        self.close()

    # ── P-Memory commands ──

    def query_pmemory_index(self, timeout=1.0):
        """Query the machine P-Memory directory.

        Sends the "PI" command terminated with CTRL_ETX. Reads the response
        until CTRL_ETB is received, then reads the 2 trailing checksum bytes.

        Args:
            timeout (float): Read timeout in seconds. Default: 1.0.

        Returns:
            bytes: Raw response bytes (payload bytes up to and including CTRL_ETB,
                   followed by the 2 ASCII-hex checksum bytes).

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: If the machine does not respond in time.
        """
        self._require_open()

        self._logger.log_info("query_pmemory_index()")

        saved_timeout = self._serial.timeout
        self._serial.timeout = timeout
        try:
            self.flush()
            self._serial.write(b"PI" + bytes([self.CTRL_ETX]))

            data = self._serial.read_until(expected=bytes([self.CTRL_ETB]))
            if not data or data[-1] != self.CTRL_ETB:
                raise MachineCommError(
                    _tr("Timeout waiting for P-Memory response (CTRL_ETB not received).")
                )

            checksum_bytes = self._serial.read(2)
            if len(checksum_bytes) < 2:
                raise MachineCommError(
                    _tr("Timeout waiting for P-Memory checksum bytes.")
                )

            self._serial.write(bytes([self.CTRL_ACK]))
            return data + checksum_bytes
        finally:
            self._serial.timeout = saved_timeout

    def delete_pmemory_slot(self, slot_index, timeout=1.0):
        """Send a delete command for the given P-Memory slot.

        Sends "PL<xx>" + CTRL_ETX where <xx> is the zero-based slot index
        encoded as two ASCII-hex characters, then waits for CTRL_ACK or CTRL_NAK.

        Args:
            slot_index (int): Zero-based index of the slot to delete.
            timeout (float): Read timeout in seconds. Default: 1.0.

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: With message _tr("Machine refused deletion") on CTRL_NAK,
                or _tr("Error during communication") on any other unexpected response.
        """
        self._require_open()

        self._logger.log_info(f"delete_pmemory_slot(slot_index={slot_index})")

        saved_timeout = self._serial.timeout
        self._serial.timeout = timeout
        try:
            self.flush()
            cmd = f"PL{slot_index:02X}".encode('ascii') + bytes([self.CTRL_ETX])
            self._serial.write(cmd)

            response = self._serial.read(1)
            if not response:
                raise MachineCommError(_tr("Error during communication"))
            if response[0] == self.CTRL_NAK:
                raise MachineCommError(_tr("Machine refused deletion"))
            if response[0] != self.CTRL_ACK:
                raise MachineCommError(_tr("Error during communication"))
        finally:
            self._serial.timeout = saved_timeout

    def load_pmemory_slot(self, slot_index, slot_type, timeout=1.0, total_size=0, progress_callback=None):
        """Load a pattern from a P-Memory slot.

        Sends 'RM06<XX><T>' + CTRL_ETX, where XX is the zero-based slot index
        as two ASCII-hex characters and T is '0' for 9mm or '1' for MAXI.

        The machine responds with a stream of hex-ASCII chunks, each terminated
        by CTRL_ETB + 2-char hex checksum.  The final chunk has CTRL_ETX
        appended after the checksum.  Each chunk is acknowledged with CTRL_ACK.

        Args:
            slot_index (int): Zero-based index of the slot to read.
            slot_type (str): '9mm' or 'MAXI'.
            timeout (float): Per-byte read timeout in seconds. Default: 1.0.

        Returns:
            bytes: Concatenated hex-ASCII payload from all chunks.

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: On NAK, checksum error, or communication timeout.
        """
        self._require_open()

        self._logger.log_info(
            f"load_pmemory_slot(slot_index={slot_index}, slot_type={slot_type}, total_size={total_size})"
        )

        if slot_type == "9mm":
            type_char = "0"
        elif slot_type == "MAXI":
            type_char = "1"
        else:
            raise MachineCommError(_tr("Unknown slot type: {0}").format(repr(slot_type)))

        saved_timeout = self._serial.timeout
        self._serial.timeout = timeout
        try:
            self.flush()
            cmd = f"RM06{slot_index:02X}{type_char}".encode('ascii') + bytes([self.CTRL_ETX])
            self._serial.write(cmd)

            # First byte: NAK means invalid request, otherwise start of first chunk
            first = self._serial.read(1)
            if not first:
                raise MachineCommError(_tr("No response from machine."))
            if first[0] == self.CTRL_NAK:
                raise MachineCommError(_tr("Machine refused the P-Memory read request."))

            receive_buffer = bytearray()
            # first byte already received; carry it into the first chunk
            carried = first

            while True:
                # Seed the chunk payload with the carried-over byte, then read until CTRL_ETB
                chunk_payload = bytearray(carried)
                carried = None

                while True:
                    b = self._serial.read(1)
                    if not b:
                        raise MachineCommError(_tr("Timeout waiting for chunk data."))
                    if b[0] == self.CTRL_ETB:
                        break
                    chunk_payload.extend(b)

                # Read 2 ASCII-hex checksum characters
                cs_bytes = self._serial.read(2)
                if len(cs_bytes) < 2:
                    raise MachineCommError(_tr("Timeout waiting for chunk checksum."))

                # Verify checksum
                try:
                    received_cs = int(cs_bytes.decode('ascii'), 16)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise MachineCommError(
                        _tr("Invalid checksum encoding: {0}").format(repr(cs_bytes))
                    ) from exc

                payload = bytes(chunk_payload)
                expected_cs = self.checksum(payload)
                if received_cs != expected_cs:
                    self._serial.write(bytes([self.CTRL_NAK]))
                    raise MachineCommError(
                        _tr("Chunk checksum mismatch: expected {0}, got {1}").format(
                            f"{expected_cs:02X}", f"{received_cs:02X}")
                    )

                receive_buffer.extend(payload)
                if progress_callback is not None:
                    progress_callback(len(receive_buffer), total_size)
                self._serial.write(bytes([self.CTRL_ACK]))

                # After ACK, read the first byte of the next chunk.
                # CTRL_ETX means the machine has no more data.
                next_b = self._serial.read(1)
                if not next_b:
                    raise MachineCommError(_tr("Timeout waiting for next chunk or end-of-data."))
                if next_b[0] == self.CTRL_ETX:
                    break
                carried = next_b  # first byte of the next chunk

            return bytes(receive_buffer)
        finally:
            self._serial.timeout = saved_timeout

    def send_pmemory_slot(self, slot_index, pattern, machine_model, chunk_size=250, timeout=1.0, progress_callback=None):
        """Dispatch to the appropriate send method based on machine_model.

        The P-Memory framing is a capability of the model, so the registry in
        :mod:`machine_models` decides which writer runs.
        """

        self._logger.log_info(f"send_pmemory_slot(slot_index={slot_index})")

        model = self._require_model(machine_model)
        if model.pmemory_variant == PMEMORY_VARIANT_1475CD:
            self.send_pmemory_slot_1475cd(slot_index, pattern,
                                          chunk_size=chunk_size, timeout=timeout,
                                          progress_callback=progress_callback)
        else:
            self.send_pmemory_slot_75xx(slot_index, pattern,
                                        chunk_size=chunk_size, timeout=timeout,
                                        progress_callback=progress_callback)

    def send_pmemory_slot_75xx(self, slot_index, pattern, chunk_size=250, timeout=1.0, progress_callback=None):
        """Write a pattern to a specific P-Memory slot in three phases.

        Phase 1 - Write command:
            ``PN<xx><yy><zzzz>`` + CTRL_ETB + 2-hex checksum + CTRL_ETX
            where xx = slot index (hex), yy = stitch-type byte (00 = 9mm, 01 = MAXI),
            zzzz = expected machine-side storage size (hex).
        Phase 2 - Header:
            header bytes + CTRL_ETB + 2-hex checksum + CTRL_ETX
        Phase 3 - Stitch data (split into chunk_size-byte chunks):
            chunk + CTRL_ETB + 2-hex checksum
            last chunk has CTRL_ETX appended after the checksum.
            CTRL_EOT is sent after the last chunk to signal end of transmission.

        The machine must reply with CTRL_ENQ after write command and header, 
        and CTRL_ACK after every chunk.

        Args:
            slot_index (int): 0-based slot number to write to.
            pattern: StitchPattern instance (stitch_type + points).
            chunk_size (int): Stitch-data payload bytes per chunk. Default: 250.
            timeout (float): Per-response read timeout in seconds. Default: 1.0.
            progress_callback: Optional ``(done_bytes, total_bytes)`` callable
                called after each stitch-data chunk is acknowledged.

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: On timeout, CTRL_NAK, or unexpected response.
        """
        self._require_open()

        saved_timeout = self._serial.timeout
        self._serial.timeout = timeout
        
        try:
            self.flush()
            stitch_type_byte = 0x00 if pattern.stitch_type == "9mm" else 0x01

            # ── Pre-compute stitch data and final machine-side points ──────
            stitch_data, final_points = self.encode_pmemory_stitch_data(pattern)
            expected_size = len(final_points) * 2 if pattern.stitch_type == "9mm" else len(final_points) * 3

            # ── Phase 1: write command ─────────────────────────────────────
            cmd_payload = (
                f"PN{slot_index:02X}{stitch_type_byte:02X}{expected_size:04X}"
            ).encode('ascii')
            cs = self.checksum(cmd_payload)

            self._logger.log_info(f"send_pmemory_slot(slot_index={slot_index}) - write command")

            self._serial.write(
                cmd_payload
                + bytes([self.CTRL_ETB])
                + f"{cs:02X}".encode('ascii')
                + bytes([self.CTRL_ETX])
            )
            resp = self._serial.read(1)
            if not resp:
                raise MachineCommError(
                    _tr("Timeout waiting for acknowledgement after write command.")
                )
            if resp[0] == self.CTRL_NAK:
                raise MachineCommError(_tr("Machine rejected the write command."))
            if resp[0] != self.CTRL_ENQ:
                raise MachineCommError(
                    _tr("Unexpected response 0x{0} after write command.").format(f"{resp[0]:02X}")
                )

            # ── Phase 2: header ────────────────────────────────────────────
            header = self.encode_pmemory_header_75xx(pattern, final_points)
            cs = self.checksum(header)

            self._logger.log_info(f"send_pmemory_slot(slot_index={slot_index}) - write header")

            self._serial.write(
                header
                + bytes([self.CTRL_ETB])
                + f"{cs:02X}".encode('ascii')
                + bytes([self.CTRL_ETX])
            )
            resp = self._serial.read(1)
            if not resp:
                raise MachineCommError(
                    _tr("Timeout waiting for acknowledgement after header.")
                )
            if resp[0] == self.CTRL_NAK:
                raise MachineCommError(_tr("Machine rejected the header."))
            if resp[0] != self.CTRL_ENQ:
                raise MachineCommError(
                    _tr("Unexpected response 0x{0} after header.").format(f"{resp[0]:02X}")
                )

            # ── Phase 3: stitch data chunks ────────────────────────────────
            total = len(stitch_data)
            for offset in range(0, total, chunk_size):
                chunk = stitch_data[offset:offset + chunk_size]
                cs = self.checksum(chunk)
                is_last_chunk = (offset + chunk_size) >= total

                self._logger.log_info(
                    f"send_pmemory_slot(slot_index={slot_index}) - write chunk {offset // chunk_size + 1}"
                )

                self._serial.write(
                    chunk
                    + bytes([self.CTRL_ETB])
                    + f"{cs:02X}".encode('ascii')
                    + (bytes([self.CTRL_ETX]) if is_last_chunk else b'')
                )
                resp = self._serial.read(1)
                if not resp:
                    raise MachineCommError(
                        _tr("Timeout waiting for acknowledgement after chunk {0}.").format(
                            offset // chunk_size + 1)
                    )
                if resp[0] == self.CTRL_NAK:
                    raise MachineCommError(_tr("Machine rejected a stitch data chunk."))
                if resp[0] != self.CTRL_ACK:
                    raise MachineCommError(
                        _tr("Unexpected response 0x{0} during stitch data transfer.").format(f"{resp[0]:02X}")
                    )
                if progress_callback is not None:
                    progress_callback(min(offset + chunk_size, total), total)
        finally:
            self._serial.timeout = saved_timeout

    def send_pmemory_slot_1475cd(self, slot_index, pattern, chunk_size=250, timeout=1.0, progress_callback=None):
        """Write a pattern to a specific P-Memory slot in three phases.

        Phase 1 - Write command:
            ``PN<xx><yy><zzzz><hhhhhhhh>`` + CTRL_ETB + 2-hex checksum + CTRL_ETX
            where xx = slot index (hex), yy = stitch-type byte (00 = 9mm, 01 = MAXI),
            zzzz = expected machine-side storage size (hex), hhhhhhhh = additional header info (hex).
        Phase 2 - Stitch data (single frame, not chunked):
            stitch_data + CTRL_ETB + 2-hex checksum + CTRL_ETX.
            The write is split into ≤100-byte pieces solely for progress reporting;
            the machine sees the full frame as one transmission.

        The machine must reply with CTRL_ENQ after write command,
        and CTRL_ACK after the stitch data frame.

        Args:
            slot_index (int): 0-based slot number to write to.
            pattern: StitchPattern instance (stitch_type + points).
            chunk_size (int): Stitch-data payload bytes per chunk. Default: 250.
            timeout (float): Per-response read timeout in seconds. Default: 1.0.
            progress_callback: Optional ``(done_bytes, total_bytes)`` callable
                called after each stitch-data chunk is acknowledged.

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: On timeout, CTRL_NAK, or unexpected response.
        """
        self._require_open()

        saved_timeout = self._serial.timeout
        self._serial.timeout = timeout
        try:
            self.flush()
            stitch_type_byte = 0x00 if pattern.stitch_type == "9mm" else 0x01

            # ── Pre-compute stitch data and final machine-side points ──────
            stitch_data, final_points = self.encode_pmemory_stitch_data(pattern)
            expected_size = len(final_points) * 2 if pattern.stitch_type == "9mm" else len(final_points) * 3

            # ── Phase 1: write command with header ─────────────────────────
            header = self.encode_pmemory_header_1475cd(pattern, final_points)
            cmd_payload = (
                f"PN{slot_index:02X}{stitch_type_byte:02X}{expected_size:04X}"
            ).encode('ascii') + header
            cs = self.checksum(cmd_payload)

            self._logger.log_info(f"send_pmemory_slot(slot_index={slot_index}) - write command")

            self._serial.write(
                cmd_payload
                + bytes([self.CTRL_ETB])
                + f"{cs:02X}".encode('ascii')
                + bytes([self.CTRL_ETX])
            )
            resp = self._serial.read(1)
            if not resp:
                raise MachineCommError(
                    _tr("Timeout waiting for acknowledgement after write command.")
                )
            if resp[0] == self.CTRL_NAK:
                raise MachineCommError(_tr("Machine rejected the write command."))
            if resp[0] != self.CTRL_ENQ:
                raise MachineCommError(
                    _tr("Unexpected response 0x{0} after write command.").format(f"{resp[0]:02X}")
                )

            # ── Phase 2: stitch data (single frame, no chunking) ──────────
            # The 1475 CD expects the entire stitch data in one frame
            # terminated by CTRL_ETB + checksum + CTRL_ETX.
            # Split _serial.write into ≤100-byte writes solely for progress reporting.
            data_cs = self.checksum(stitch_data)
            frame = stitch_data + bytes([self.CTRL_ETB]) + f"{data_cs:02X}".encode('ascii') + bytes([self.CTRL_ETX])
            total = len(stitch_data)

            self._logger.log_info(f"send_pmemory_slot(slot_index={slot_index}) - write stitch data")

            for offset in range(0, len(frame), 100):
                piece = frame[offset:offset + 100]
                time.sleep(0.01)  # slight delay to avoid overwhelming the machine
                self._serial.write(piece)
                if progress_callback is not None:
                    # Progress based on stitch_data bytes written so far
                    done = min(offset, total)
                    progress_callback(done, total)

            # Final progress: all stitch_data has been sent
            if progress_callback is not None:
                progress_callback(total, total)

            resp = self._serial.read(1)
            if not resp:
                raise MachineCommError(
                    _tr("Timeout waiting for acknowledgement after stitch data.")
                )
            if resp[0] == self.CTRL_NAK:
                raise MachineCommError(_tr("Machine rejected the stitch data."))
            if resp[0] != self.CTRL_ACK:
                raise MachineCommError(
                    _tr("Unexpected response 0x{0} after stitch data.").format(f"{resp[0]:02X}")
                )
        finally:
            self._serial.timeout = saved_timeout

    # ── Memory Card commands ──

    def query_card_index(self, timeout=1.0):
        """Query the machine memory card index.

        Sends "KI" + CTRL_ETX and reads the response.

        If the machine replies with CTRL_NAK + CTRL_BS there is no card
        inserted; CTRL_EOT is sent and MachineCommError is raised.

        Response format (raw bytes before CTRL_ETB)::

            CTRL_ACK + 00 00 <CardNo[2]> <PayloadSize>
            01 <Offs9mm> <N9mm> 03 <OffsEmbr> <NEmbr>
            00*6 02 <OffsMaxi> <NMaxi> 00*9 <PayloadSize>

            + CTRL_ETB + checksum

        Args:
            timeout (float): Read timeout in seconds. Default: 2.0.

        Returns:
            dict: {
                'card_no_bytes': bytes  - raw 2-byte card number,
                'card_no':       int    - card number (big-endian),
                'n_9mm':         int    - number of 9mm patterns on the card,
                'n_maxi':        int    - number of MAXI patterns on the card,
                'n_embr':        int    - number of Embroidery patterns on the card,
            }

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: If no card is inserted, checksum fails, or
                the response is malformed.
        """
        self._require_open()

        self._logger.log_info("query_card_index()")

        saved_timeout = self._serial.timeout
        self._serial.timeout = timeout
        try:
            self.flush()
            self._serial.write(b"KI" + bytes([self.CTRL_ETX]))

            # First byte tells us whether a card is present
            first = self._serial.read(1)
            if not first:
                raise MachineCommError(_tr("No response to card query command."))

            if first[0] == self.CTRL_NAK:
                # Read the second byte to determine the reason for NAK
                err_code = self._serial.read(1)
                if not err_code:
                    raise MachineCommError(_tr("No response to card query command."))

                if err_code[0] == 0x00:
                    # 0x00 — card is copyright protected ("read-only stitch card")
                    raise MachineCommError(
                        _tr("The card is copyright protected. "
                            "It is not allowed to read or modify the content of this card.")
                    )
                elif err_code[0] == 0x08:
                    # 0x08 — no memory card inserted
                    raise MachineCommError(_tr("No memory card inserted in the machine."))
                elif err_code[0] == 0x09:
                    # 0x09 — card is not initialized
                    raise MachineCommError(
                        _tr("The memory card is not initialized. "
                            "Please initialize the card and try again."))
                else:
                    # Unknown second byte — treat as an unexpected response
                    raise MachineCommError(
                        _tr("Unexpected response to card query: NAK followed by 0x{0}.").format(
                            f"{err_code[0]:02X}")
                    )

            # Valid response: buf[0] should be 0x06
            buf = bytearray([first[0]])

            # bytes 1-4: 0x00 0x00 CardNo[0] CardNo[1]
            rest_header = self._serial.read(4)
            if len(rest_header) < 4:
                raise MachineCommError(_tr("Timeout reading card query response header."))
            buf.extend(rest_header)

            # byte 5: PayloadSize
            ps_raw = self._serial.read(1)
            if not ps_raw:
                raise MachineCommError(_tr("Timeout reading PayloadSize in card query response."))
            payload_size = ps_raw[0]
            buf.extend(ps_raw)

            # bytes 6 … 6+payload_size-1: payload
            payload = self._serial.read(payload_size)
            if len(payload) < payload_size:
                raise MachineCommError(_tr("Timeout reading payload in card query response."))
            buf.extend(payload)

            # PayloadSize
            ps2_raw = self._serial.read(1)
            if not ps2_raw:
                raise MachineCommError(_tr("Timeout reading PayloadSize in card query response."))
            buf.extend(ps2_raw)

            # CTRL_ETB
            etb = self._serial.read(1)
            if not etb or etb[0] != self.CTRL_ETB:
                raise MachineCommError(_tr("Expected CTRL_ETB in card query response."))

            # 2 ASCII-hex checksum characters
            cs_raw = self._serial.read(2)
            if len(cs_raw) < 2:
                raise MachineCommError(_tr("Timeout reading checksum in card query response."))

            received_cs = int(cs_raw.decode('ascii'), 16)

            expected_cs = self.checksum(bytes(buf[1:]))
            if received_cs != expected_cs:
                raise MachineCommError(
                    _tr("Card query checksum mismatch: expected {0}, got {1}.").format(
                        f"{expected_cs:02X}", f"{received_cs:02X}")
                )

            # Parse fields from buf
            # [0]=0x06 [1]=0x00 [2]=0x00 [3..4]=CardNo [5]=PayloadSize
            # payload: [0]=0x01 [1]=0x00 [2]=N9mm [3]=0x03 [4]=0xC8 [5]=NEmbr
            #          [6..11]=0x00*6 [12]=0x02 [13]=0x00 [14]=NMaxi
            #          [15..23]=0x00*9 [24]=PayloadSize(repeat)
            if len(buf) < 21:
                raise MachineCommError(
                    _tr("Card query response payload too short: {0} bytes.").format(len(buf))
                )

            card_no_bytes = bytes(buf[3:5])
            # Convert 0x10 0x02 -> 1002 (dec); check if this is how machine really shows it on display
            card_no = int(''.join(f'{b:02x}' for b in card_no_bytes))
            # offset into buf for the payload fields (buf[6] = payload[0])
            offs_9mm = buf[7]
            n_9mm = buf[8]   # payload byte 2
            offs_embr = buf[10]
            n_embr = buf[11]  # payload byte 5
            offs_maxi = buf[19]
            n_maxi = buf[20]  # payload byte 14

            return {
                'card_no_bytes': card_no_bytes,
                'card_no':       card_no,
                'n_9mm':         n_9mm,
                'n_maxi':        n_maxi,
                'n_embr':        n_embr,
                'offs_9mm':      offs_9mm,
                'offs_maxi':     offs_maxi,
                'offs_embr':     offs_embr,
            }
        finally:
            self._serial.timeout = saved_timeout

    def query_card_preview(self, card_no_bytes, slot_index, pattern_type, timeout=1.0, max_retries=3):
        """Request and receive a preview image for one card pattern.

        Sends::

            CTRL_ETX + "KB" + 0x00 0x00 + <CardNo[2]>
            + <BANK> + <SLOT> + <TYPE> + 0x00 + CTRL_ETX

        Type / bank / slot encoding:

        ============= ====== ========================
        pattern_type  TYPE   BANK   SLOT
        ============= ====== ========================
        9mm           0x01   0xC0   slot (usually 0-based)
        MAXI          0x02   0xD0   slot (usually 0-based)
        Embroidery    0x03   0xC0   slot (usually offset by 0xC8 but the values comes from the card query response)
        ============= ====== ========================

        The machine replies in one or more chunks.

        *First chunk*::

            CTRL_ACK + <descriptor[4]> + SIZE(2) + NAME_SIZE(1) + NAME
            + PayloadSize(1) + Payload + PayloadSize(1) + CTRL_ETB
            + checksum

        The descriptor is ``<colour count> + <colour mask BE16> + <hoop byte>``,
        taken from the slot's write header: the count is the number of thread
        colours the design uses (1 for 9 mm / MAXI), the mask marks the palette
        colours in use, and the hoop byte is 1 for the small and 2 for the large
        hoop.

        *Following chunks*::

            CTRL_ENQ + PayloadSize(1) + Payload + PayloadSize(1)
            + CTRL_ETB + checksum

        On a good checksum send CTRL_ACK; on a bad one send CTRL_NAK and the
        machine retransmits the chunk.  After the final CTRL_ACK the machine
        sends CTRL_ETX to indicate end of transfer for this pattern.

        Args:
            card_no_bytes (bytes): Raw 2-byte card number from query_card().
            slot (int): Zero-based slot index within the pattern type.
            pattern_type (str): ``'9mm'``, ``'MAXI'``, or ``'Embroidery'``.
            timeout (float): Per-read timeout in seconds. Default: 5.0.
            max_retries (int): Maximum chunk retransmit attempts. Default: 3.

        Returns:
            dict: {
                'name':         str  - pattern name (ASCII),
                'size':         int  - total stitch data size in bytes on card,
                'pattern_type': str  - pattern type,
                'slot':         int  - zero-based slot index,
                'preview_hex':  str  - preview payload as a hex string,
                'hoop':         str | None - 'small hoop' / 'large hoop' when the
                                descriptor names one, else None,
                'colour_count': int  - thread colours the design uses,
                'colour_mask':  int  - 16-bit mask of the palette colours in use,
            }

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: On timeout, unexpected response, or checksum error
                after all retries.
        """
        self._require_open()

        card_id = ''.join(f'{b:02X}' for b in card_no_bytes)
        self._logger.log_info(
            f"query_card_preview(card_no={card_id}, slot_index={slot_index}, pattern_type={pattern_type})"
        )

        type_byte_map = {'9mm': 0x01, 'MAXI': 0x02, 'Embroidery': 0x03}
        if pattern_type not in type_byte_map:
            raise MachineCommError(_tr("Unknown pattern type: {0}").format(repr(pattern_type)))

        type_byte = type_byte_map[pattern_type]
        bank_byte = 0xD0 if pattern_type == 'MAXI' else 0xC0
        # slot_byte = (slot + 0xC8) if pattern_type == 'Embroidery' else slot
        slot_byte = slot_index

        cmd = (
            bytes([self.CTRL_ETX]) + b"KB" +
            bytes([0x00, 0x00]) +
            card_no_bytes +
            bytes([bank_byte, slot_byte, type_byte, 0x00, self.CTRL_ETX])
        )

        saved_timeout = self._serial.timeout
        self._serial.timeout = timeout
        try:
            self.flush()
            self._serial.write(cmd)

            name = ''
            size = 0
            colour_count = 0
            colour_mask = 0
            hoop = None
            all_payload = bytearray()

            # ── First chunk (CTRL_ACK) ────────────────────────────────────
            for attempt in range(max_retries + 1):
                fb = self._serial.read(1)
                if not fb:
                    raise MachineCommError(
                        _tr("No response to card preview command ({0} slot {1}).").format(
                            pattern_type, slot_index)
                    )
                if fb[0] == self.CTRL_NAK:
                    raise MachineCommError(
                        _tr("Machine rejected card preview request ({0} slot {1}).").format(
                            pattern_type, slot_index)
                    )
                if fb[0] != self.CTRL_ACK:
                    raise MachineCommError(
                        _tr("Expected CTRL_ACK to start first chunk, got 0x{0}.").format(
                            f"{fb[0]:02X}")
                    )

                descriptor = self._serial.read(4)
                if len(descriptor) < 4:
                    raise MachineCommError(
                        _tr("Timeout reading the pattern descriptor in first chunk."))

                size_bytes = self._serial.read(2)
                if len(size_bytes) < 2:
                    raise MachineCommError(_tr("Timeout reading SIZE in first chunk."))

                name_size_raw = self._serial.read(1)
                if not name_size_raw:
                    raise MachineCommError(_tr("Timeout reading NAME_SIZE in first chunk."))
                name_size = name_size_raw[0]

                name_raw = self._serial.read(name_size) if name_size > 0 else b''
                if len(name_raw) < name_size:
                    raise MachineCommError(_tr("Timeout reading NAME in first chunk."))

                ps_raw = self._serial.read(1)
                if not ps_raw:
                    raise MachineCommError(_tr("Timeout reading PayloadSize in first chunk."))
                ps = ps_raw[0]

                chunk_payload = self._serial.read(ps)
                if len(chunk_payload) < ps:
                    raise MachineCommError(_tr("Timeout reading payload in first chunk."))

                ps_repeat_raw = self._serial.read(1)
                if not ps_repeat_raw:
                    raise MachineCommError(_tr("Timeout reading repeated PayloadSize in first chunk."))

                etb_raw = self._serial.read(1)
                if not etb_raw or etb_raw[0] != self.CTRL_ETB:
                    raise MachineCommError(_tr("Expected CTRL_ETB in first chunk."))

                cs_raw = self._serial.read(2)
                if len(cs_raw) < 2:
                    raise MachineCommError(_tr("Timeout reading checksum in first chunk."))

                cs_data = (descriptor + size_bytes + name_size_raw
                           + name_raw + ps_raw + chunk_payload + ps_repeat_raw)
                received_cs = int(cs_raw.decode('ascii'), 16)

                expected_cs = self.checksum(cs_data)

                if received_cs == expected_cs:
                    name = name_raw.rstrip(b'\x00').decode('latin-1', errors='replace')
                    size = int.from_bytes(size_bytes, 'big')
                    colour_count, colour_mask, hoop = (
                        MachineComm._parse_card_preview_descriptor(descriptor))
                    all_payload.extend(chunk_payload)
                    self._serial.write(bytes([self.CTRL_ACK]))
                    break
                else:
                    self._serial.write(bytes([self.CTRL_NAK]))
                    if attempt == max_retries:
                        raise MachineCommError(
                            _tr("First chunk checksum mismatch ({0} slot {1}) after {2} retries.").format(
                                pattern_type, slot_index, max_retries)
                        )
                    # Machine retransmits starting with CTRL_ACK; loop retries

            # ── Following chunks ──────────────────────────────────────────
            while True:
                nb = self._serial.read(1)
                if not nb:
                    raise MachineCommError(
                        _tr("Timeout waiting for next chunk or end marker.")
                    )

                if nb[0] == self.CTRL_ETX:
                    break  # Transfer complete for this pattern

                if nb[0] != self.CTRL_ENQ:
                    raise MachineCommError(
                        _tr("Unexpected byte 0x{0} in chunk stream.").format(f"{nb[0]:02X}")
                    )

                for attempt in range(max_retries + 1):
                    ps_raw = self._serial.read(1)
                    if not ps_raw:
                        raise MachineCommError(_tr("Timeout reading PayloadSize in chunk."))
                    ps = ps_raw[0]

                    chunk_payload = self._serial.read(ps)
                    if len(chunk_payload) < ps:
                        raise MachineCommError(_tr("Timeout reading payload in chunk."))

                    ps_repeat_raw = self._serial.read(1)
                    if not ps_repeat_raw:
                        raise MachineCommError(_tr("Timeout reading repeated PayloadSize in chunk."))

                    etb_raw = self._serial.read(1)
                    if not etb_raw or etb_raw[0] != self.CTRL_ETB:
                        raise MachineCommError(_tr("Expected CTRL_ETB in chunk."))

                    cs_raw = self._serial.read(2)
                    if len(cs_raw) < 2:
                        raise MachineCommError(_tr("Timeout reading checksum in chunk."))

                    cs_data = nb + ps_raw + chunk_payload + ps_repeat_raw
                    received_cs = int(cs_raw.decode('ascii'), 16)

                    expected_cs = self.checksum(cs_data)

                    if received_cs == expected_cs:
                        all_payload.extend(chunk_payload)
                        self._serial.write(bytes([self.CTRL_ACK]))
                        break
                    else:
                        self._serial.write(bytes([self.CTRL_NAK]))
                        if attempt == max_retries:
                            raise MachineCommError(
                                _tr("Chunk checksum mismatch ({0} slot {1}) after {2} retries.").format(
                                    pattern_type, slot_index, max_retries)
                            )
                        # Machine retransmits CTRL_ENQ; re-read it before inner loop retry
                        enq_retry = self._serial.read(1)
                        if not enq_retry or enq_retry[0] != self.CTRL_ENQ:
                            raise MachineCommError(_tr("Expected CTRL_ENQ on retransmit."))

            return {
                'name':         name,
                'size':         size,
                'pattern_type': pattern_type,
                'slot':         slot_index,
                'preview_hex':  all_payload.hex(),
                'hoop':         hoop,
                'colour_count': colour_count,
                'colour_mask':  colour_mask,
            }
        finally:
            self._serial.timeout = saved_timeout

    def load_card_slot(self, card_no_bytes, slot_index, pattern_type, timeout=1.0, max_retries=3, total_size=0, progress_callback=None):
        """Load (read) a pattern from a memory card slot.

        Sends::

            CTRL_ETX + "KS" + 0x00 0x00 + <CardNo[2]>
            + <BANK> + <SLOT> + <TYPE> + CTRL_ETX

        Type / bank encoding:

        ============= ====== ======
        pattern_type  TYPE   BANK
        ============= ====== ======
        9mm           0x01   0xC0
        MAXI          0x02   0xD0
        Embroidery    0x03   0xC0
        ============= ====== ======

        The machine confirms with CTRL_ACK (or CTRL_NAK = rejected).

        *First chunk* (after CTRL_ACK):
            ``<4 unknown bytes> + SIZE + <SIZE payload bytes> + SIZE + CTRL_ETB + <2 hex checksum>``

            The first 4 bytes are discarded; SIZE is the fifth
            byte and denotes the payload length that follows.
            Checksum covers ``SIZE + payload + SIZE``.

        *Subsequent chunks*:
            ``CTRL_ENQ + SIZE + <SIZE payload bytes> + SIZE + CTRL_ETB + <2 hex checksum>``

            Checksum covers ``SIZE + payload + SIZE``.

        For each good chunk send CTRL_ACK; for a bad checksum send CTRL_NAK
        so the machine retransmits.  Transfer ends when the machine sends
        CTRL_ETX after the final CTRL_ACK.

        Args:
            card_no_bytes (bytes): Raw 2-byte card number from query_card().
            slot_index (int): Zero-based absolute slot index on the card (offset
                already applied).
            pattern_type (str): ``'9mm'``, ``'MAXI'``, or ``'Embroidery'``.
            timeout (float): Per-read timeout in seconds. Default: 1.0.
            max_retries (int): Maximum chunk retransmit attempts. Default: 3.
            total_size (int): Expected total payload size in bytes, used as the
                ``total_bytes`` argument passed to *progress_callback*.  Pass
                ``0`` (default) when the size is unknown.
            progress_callback: Optional ``(received_bytes, total_bytes)``
                callable; total_bytes is 0 when unknown.

        Returns:
            bytes: Concatenated raw stitch-data payload from all chunks.

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: On CTRL_NAK, checksum error after all retries,
                or timeout.
        """
        self._require_open()

        card_id = ''.join(f'{b:02X}' for b in card_no_bytes)
        self._logger.log_info(
            f"load_card_slot(card_no={card_id}, slot_index={slot_index}, pattern_type={pattern_type}, total_size={total_size})"
        )

        type_byte_map = {'9mm': 0x01, 'MAXI': 0x02, 'Embroidery': 0x03}
        if pattern_type not in type_byte_map:
            raise MachineCommError(_tr("Unknown pattern type: {0}").format(repr(pattern_type)))

        type_byte = type_byte_map[pattern_type]
        bank_byte = 0xD0 if pattern_type == 'MAXI' else 0xC0

        cmd = (
            bytes([self.CTRL_ETX]) + b"KS" +
            bytes([0x00, 0x00]) +
            card_no_bytes +
            bytes([bank_byte, slot_index, type_byte, self.CTRL_ETX])
        )

        saved_timeout = self._serial.timeout
        self._serial.timeout = timeout
        try:
            self.flush()
            self._serial.write(cmd)

            # Machine should reply with CTRL_ACK to accept the request
            resp = self._serial.read(1)
            if not resp:
                raise MachineCommError(
                    _tr("No response to load command ({0} slot {1}).").format(pattern_type, slot_index)
                )
            if resp[0] == self.CTRL_NAK:
                raise MachineCommError(
                    _tr("Machine rejected load command ({0} slot {1}).").format(pattern_type, slot_index)
                )
            if resp[0] != self.CTRL_ACK:
                raise MachineCommError(
                    _tr("Unexpected response 0x{0} to load command ({1} slot {2}).").format(
                        f"{resp[0]:02X}", pattern_type, slot_index)
                )

            all_data = bytearray()

            # ── First chunk ──────────────────────────────────────────────
            for attempt in range(max_retries + 1):
                # Skip first 4 bytes of padding, present on the first attempt;
                # retransmissions after CTRL_NAK omit them.
                if attempt == 0:
                    padding = self._serial.read(4)
                else:
                    padding = b''
                b = self._serial.read(1)
                if not b:
                    raise MachineCommError(
                        _tr("Timeout waiting for SIZE byte in first chunk.")
                    )
                size_byte = b[0]

                ps = size_byte
                chunk_payload = self._serial.read(ps)
                if len(chunk_payload) < ps:
                    raise MachineCommError(_tr("Timeout reading payload in first chunk."))

                ps_repeat_raw = self._serial.read(1)
                if not ps_repeat_raw:
                    raise MachineCommError(
                        _tr("Timeout reading repeated SIZE in first chunk.")
                    )

                etb_raw = self._serial.read(1)
                if not etb_raw or etb_raw[0] != self.CTRL_ETB:
                    raise MachineCommError(
                        _tr("Expected CTRL_ETB in first chunk, got 0x{0}.").format(
                            f"{etb_raw[0] if etb_raw else 0:02X}")
                    )

                cs_raw = self._serial.read(2)
                if len(cs_raw) < 2:
                    raise MachineCommError(_tr("Timeout reading checksum in first chunk."))

                cs_data = padding + bytes([size_byte]) + chunk_payload + ps_repeat_raw
                received_cs = int(cs_raw.decode('ascii'), 16)
                expected_cs = self.checksum(cs_data)

                if received_cs == expected_cs:
                    all_data.extend(chunk_payload)
                    if progress_callback:
                        progress_callback(len(all_data), total_size)
                    self._serial.write(bytes([self.CTRL_ACK]))
                    break
                else:
                    self._serial.write(bytes([self.CTRL_NAK]))
                    if attempt == max_retries:
                        raise MachineCommError(
                            _tr("First chunk checksum mismatch ({0} slot {1}) after {2} retries.").format(
                                pattern_type, slot_index, max_retries)
                        )
                    # Machine will retransmit; loop to try again

            # ── Subsequent chunks ────────────────────────────────────────
            while True:
                nb = self._serial.read(1)
                if not nb:
                    raise MachineCommError(
                        _tr("Timeout waiting for next chunk marker.")
                    )

                if nb[0] == self.CTRL_ETX:
                    break  # All data received

                if nb[0] != self.CTRL_ENQ:
                    raise MachineCommError(
                        _tr("Unexpected byte 0x{0} expecting CTRL_ENQ or CTRL_ETX.").format(f"{nb[0]:02X}")
                    )

                for attempt in range(max_retries + 1):
                    ps_raw = self._serial.read(1)
                    if not ps_raw:
                        raise MachineCommError(_tr("Timeout reading SIZE in chunk."))
                    ps = ps_raw[0]

                    chunk_payload = self._serial.read(ps)
                    if len(chunk_payload) < ps:
                        raise MachineCommError(_tr("Timeout reading payload in chunk."))

                    ps_repeat_raw = self._serial.read(1)
                    if not ps_repeat_raw:
                        raise MachineCommError(_tr("Timeout reading repeated SIZE in chunk."))

                    etb_raw = self._serial.read(1)
                    if not etb_raw or etb_raw[0] != self.CTRL_ETB:
                        raise MachineCommError(
                            _tr("Expected CTRL_ETB in chunk, got 0x{0}.").format(
                                f"{etb_raw[0] if etb_raw else 0:02X}")
                        )

                    cs_raw = self._serial.read(2)
                    if len(cs_raw) < 2:
                        raise MachineCommError(_tr("Timeout reading checksum in chunk."))

                    cs_data = nb + ps_raw + chunk_payload + ps_repeat_raw
                    received_cs = int(cs_raw.decode('ascii'), 16)
                    expected_cs = self.checksum(cs_data)

                    if received_cs == expected_cs:
                        all_data.extend(chunk_payload)
                        if progress_callback:
                            progress_callback(len(all_data), total_size)
                        self._serial.write(bytes([self.CTRL_ACK]))
                        break
                    else:
                        self._serial.write(bytes([self.CTRL_NAK]))
                        if attempt == max_retries:
                            raise MachineCommError(
                                _tr("Chunk checksum mismatch ({0} slot {1}) after {2} retries.").format(
                                    pattern_type, slot_index, max_retries)
                            )
                        # Machine retransmits starting with CTRL_ENQ; re-read it
                        enq_retry = self._serial.read(1)
                        if not enq_retry or enq_retry[0] != self.CTRL_ENQ:
                            raise MachineCommError(
                                _tr("Expected CTRL_ENQ on chunk retransmit.")
                            )

            return bytes(all_data)
        finally:
            self._serial.timeout = saved_timeout

    def delete_card_slot(self, card_no_bytes, slot_index, pattern_type, timeout=1.0):
        """Delete a pattern from the memory card.

        Sends::

            CTRL_ETX + "KL" + 0x00 0x00 + <CardNo[2]>
            + <BANK> + <SLOT> + <TYPE> + CTRL_ETX

        Type / bank encoding:

        ============= ====== ======
        pattern_type  TYPE   BANK
        ============= ====== ======
        9mm           0x01   0xC0
        MAXI          0x02   0xD0
        Embroidery    0x03   0xC0
        ============= ====== ======

        The machine confirms deletion with CTRL_ACK.  CTRL_NAK indicates
        failure (e.g. write-protected card).

        Args:
            card_no_bytes (bytes): Raw 2-byte card number from
                :meth:`query_card`.
            slot_index (int): Absolute slot index on the card (offset already
                applied; use the ``'slot'`` value stored in the pattern dict
                returned by :meth:`query_card_preview`).
            pattern_type (str): ``'9mm'``, ``'MAXI'``, or ``'Embroidery'``.
            timeout (float): Read timeout in seconds.  Default: 5.0.

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: If the machine responds with CTRL_NAK, returns
                an unexpected byte, or times out.
        """
        self._require_open()

        card_id = ''.join(f'{b:02X}' for b in card_no_bytes)
        self._logger.log_info(
            f"delete_card_slot(card_no={card_id}, slot_index={slot_index}, pattern_type={pattern_type})"
        )

        type_byte_map = {'9mm': 0x01, 'MAXI': 0x02, 'Embroidery': 0x03}
        if pattern_type not in type_byte_map:
            raise MachineCommError(_tr("Unknown pattern type: {0}").format(repr(pattern_type)))

        type_byte = type_byte_map[pattern_type]
        bank_byte = 0xD0 if pattern_type == 'MAXI' else 0xC0

        cmd = (
            bytes([self.CTRL_ETX]) + b"KL" +
            bytes([0x00, 0x00]) +
            card_no_bytes +
            bytes([bank_byte, slot_index, type_byte, self.CTRL_ETX])
        )

        saved_timeout = self._serial.timeout
        self._serial.timeout = timeout
        try:
            self.flush()
            self._serial.write(cmd)

            resp = self._serial.read(1)
            if not resp:
                raise MachineCommError(
                    _tr("No response to delete command ({0} slot {1}).").format(
                        pattern_type, f"{slot_index:#04x}")
                )
            if resp[0] == self.CTRL_NAK:
                raise MachineCommError(
                    _tr("Machine rejected delete command ({0} slot {1}). The card may be write-protected.").format(
                        pattern_type, f"{slot_index:#04x}")
                )
            if resp[0] != self.CTRL_ACK:
                raise MachineCommError(
                    _tr("Unexpected response 0x{0} to delete command ({1} slot {2}).").format(
                        f"{resp[0]:02X}", pattern_type, f"{slot_index:#04x}")
                )
        finally:
            self._serial.timeout = saved_timeout

    def send_card_slot(self, card_no_bytes, pattern, filename, chunk_size=128, timeout=1.0, max_retries=3, progress_callback=None):
        """Write a new pattern to the next free card slot (KN command).

        The machine assigns the physical slot automatically; the caller
        specifies the card, filename and stitch data.

        KN command frame::

            CTRL_ETX + "KN" + 0x00 0x00 + <CardNo[2]>
            + <BANK> + 0x00 + <TYPE>
            + d0x_min_abs[2] + pn_x[2] + span_x[2] + y_min_to_bound[2]
            + span_y[2] + 0x0000[2] + unknown_1 + 0x0000[2] + unknown_2 + dx_abs_max
            + size_preview[2] + 0x01 + size_pattern[2] + size_name[1]
            + CTRL_ETX

        All multi-byte values are big-endian.
        BANK is 0xC0 for 9mm/Embroidery and 0xD0 for MAXI.
        TYPE is 0x01 for 9mm, 0x02 for MAXI, 0x03 for Embroidery.

        Machine acknowledges with CTRL_ACK + <BANK> + <SLOT>,
        or CTRL_NAK if the card is full or write-protected.

        Data payload = ``<filename_bytes> + <preview_bytes> + <pattern_raw>``

        The whole data payload must fit the slot's capacity
        (:attr:`CARD_MAX_PAYLOAD`); an oversized embroidery design is rejected
        here, before the machine is contacted.

        Data chunks::

            CTRL_ENQ + SIZE + <payload> + SIZE + CTRL_ETB + <2 hex checksum>

        Checksum covers: CTRL_ENQ + SIZE_BYTE + payload + SIZE_BYTE.
        Machine acknowledges each chunk with CTRL_ACK; on CTRL_NAK the chunk
        is retransmitted (max 3 retries).

        Args:
            card_no_bytes (bytes): Raw 2-byte card number from query_card().
            pattern: StitchPattern instance used to derive KN header parameters.
            filename (str): Filename (max 32 characters).
            timeout (float): Per-read timeout in seconds.  Default: 1.0.
            max_retries (int): Maximum number of retries for each chunk.  Default: 3.
            progress_callback: Optional ``(done_bytes, total_bytes)`` callable.

        Returns:
            int: The slot number assigned by the machine.

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: On timeout, CTRL_NAK, checksum error, unexpected
                response, or when the pattern payload exceeds the card slot
                capacity.
        """
        self._require_open()

        card_id = ''.join(f'{b:02X}' for b in card_no_bytes)
        self._logger.log_info(f"send_card_slot(card_no={card_id}, filename={filename!r})")

        stitch_type = pattern.stitch_type

        # Null-terminated, max 33 bytes (32 chars + '\0')
        # PCD supported max. 8 chars, but the machine seems to accept more
        # Here we truncate to 32 chars which should be enough for most use cases
        filename_bytes = filename[:32].encode('latin-1', errors='replace') + b'\x00'

        # ── Build preview image(s), pattern data and header (before connecting) ──
        if stitch_type == '9mm':
            preview_bytes = self.encode_card_preview(pattern)
            stitch_data, final_points = MachineComm.encode_card_pattern_9mm(pattern)
            header = self.encode_card_header(
                pattern, card_no_bytes, preview_bytes, stitch_data, filename_bytes,
                points=final_points)
        elif stitch_type == 'MAXI':
            preview_bytes = self.encode_card_preview(pattern)
            stitch_data, final_points = MachineComm.encode_card_pattern_maxi(pattern)
            header = self.encode_card_header(
                pattern, card_no_bytes, preview_bytes, stitch_data, filename_bytes,
                points=final_points)
        elif stitch_type in MachineComm.EMBROIDERY_HOOPS:
            # Embroidery carries one extra preview block per colour.
            preview_bytes = b"".join(MachineComm.encode_card_previews_embroidery(pattern))
            stitch_data, _points = MachineComm.encode_card_pattern_embroidery(pattern)

            # Check the complete payload before a connection is opened: the
            # machine would otherwise accept the KN command and then run out
            # of slot space halfway through the transfer.
            payload_size = len(filename_bytes) + len(preview_bytes) + len(stitch_data)
            if payload_size > MachineComm.CARD_MAX_PAYLOAD:
                self._logger.log_error(
                    f"Embroidery payload too large: {payload_size} bytes "
                    f"(limit {MachineComm.CARD_MAX_PAYLOAD})"
                )
                raise MachineCommError(_tr(
                    "The pattern file size is too large and cannot be stored on "
                    "the memory card: {0} bytes are needed, but a card slot holds "
                    "at most {1} bytes. Split the design into smaller parts."
                ).format(payload_size, MachineComm.CARD_MAX_PAYLOAD))

            header = MachineComm.encode_card_header_embroidery(
                pattern, card_no_bytes, preview_bytes, stitch_data, filename_bytes)
        else:
            raise MachineCommError(_tr("Unsupported stitch type for card writing: {0}").format(repr(stitch_type)))

        cmd = (
            bytes([self.CTRL_ETX]) + b"KN" +
            header +
            bytes([self.CTRL_ETX])
        )

        saved_timeout = self._serial.timeout
        self._serial.timeout = timeout
        try:
            self.flush()
            self._serial.write(cmd)

            # Machine replies: CTRL_ACK + <BANK> + <SLOT>
            resp = self._serial.read(3)
            if not resp:
                raise MachineCommError(
                    _tr("No response to card write command.")
                )
            
            if resp[0] == self.CTRL_NAK:
                err_code = resp[1] if len(resp) > 1 else None

                if not err_code:
                    raise MachineCommError(
                        _tr("Machine rejected the card write command.")
                    )

                if err_code == 0x00:
                    # 0x00 — card is copyright protected ("read-only stitch card")
                    raise MachineCommError(
                        _tr("The card is copyright protected. "
                            "It is not allowed to read or modify the content of this card.")
                    )
                elif err_code == 0x04:
                    # 0x04 — card is write-protected (write-protect tab on the card is set)
                    raise MachineCommError(
                        _tr("The memory card is write-protected. "
                            "Please switch the write-protection off (slide write-protect tab on the card) and try again."))
                elif err_code == 0x05:
                    # 0x05 — card is not writable
                    raise MachineCommError(
                        _tr("The memory card is not writable. "
                            "Please use a writable card and try again."))
                elif err_code == 0x08:
                    # 0x08 — no memory card inserted
                    raise MachineCommError(_tr("No memory card inserted in the machine."))
                elif err_code == 0x09:
                    # 0x09 — card is not initialized
                    raise MachineCommError(
                        _tr("The memory card is not initialized. "
                            "Please initialize the card and try again."))
                elif err_code == 0x0C:
                    # 0x0C — card has not enough free space
                    raise MachineCommError(
                        _tr("The memory card has not enough free space."))
                else:
                    # Unknown second byte — treat as an unexpected response
                    raise MachineCommError(
                        _tr("Unexpected response to card query: NAK followed by 0x{0}.").format(
                            f"{err_code:02X}")
                    )

            if resp[0] != self.CTRL_ACK:
                raise MachineCommError(
                    _tr("Unexpected response 0x{0} to card write command.").format(f"{resp[0]:02X}")
                )
            if len(resp) < 3:
                raise MachineCommError(
                    _tr("Incomplete acknowledgement to card write command ({0} byte(s) received, expected 3).").format(
                        len(resp))
                )
            assigned_slot = resp[2]

            # ── Send data in chunks ───────────────────────────────────────
            data  = bytes(filename_bytes) + bytes(preview_bytes) + bytes(stitch_data)
            total = len(data)
            done  = 0

            for offset in range(0, max(total, 1), chunk_size):
                chunk     = data[offset:offset + chunk_size]
                size_byte = len(chunk)
                cs_input  = bytes([size_byte]) + chunk + bytes([size_byte])
                cs_hex    = f"{self.checksum(cs_input):02X}".encode('ascii')

                for attempt in range(max_retries + 1):
                    if offset == 0 and attempt == 0:
                        frame = (
                            bytes([size_byte]) +
                            chunk +
                            bytes([size_byte, self.CTRL_ETB]) +
                            cs_hex
                        )
                    else:
                        frame = (
                            bytes([self.CTRL_ENQ, size_byte]) +
                            chunk +
                            bytes([size_byte, self.CTRL_ETB]) +
                            cs_hex
                        )
                    self._serial.write(frame)
                    ack = self._serial.read(1)
                    if not ack:
                        raise MachineCommError(
                            _tr("Timeout waiting for acknowledgement after chunk {0}.").format(
                                offset // chunk_size + 1)
                        )
                    if ack[0] == self.CTRL_ACK:
                        done += size_byte
                        if progress_callback is not None:
                            progress_callback(min(done, total), total)
                        break
                    if ack[0] == self.CTRL_NAK:
                        if attempt == max_retries:
                            raise MachineCommError(
                                _tr("Machine rejected chunk {0} after {1} retries.").format(
                                    offset // chunk_size + 1, max_retries)
                            )
                        # Retry the same chunk
                        continue
                    raise MachineCommError(
                        _tr("Unexpected response 0x{0} after chunk {1}.").format(
                            f"{ack[0]:02X}", offset // chunk_size + 1)
                    )
            # After the final chunk, send CTRL_ETX to indicate completion
            self._serial.write(bytes([self.CTRL_ETX]))
            return assigned_slot
        finally:
            self._serial.timeout = saved_timeout

    # ── Memory Card encoding/decoding ──

    @staticmethod
    def decode_card_pattern_9mm(raw_bytes):
        """Decode raw bytes from a 9mm memory card slot into stitch coordinates.

        Format:
          - Leading sentinel byte (0x80) is discarded.
          - Last four bytes are discarded (they appear to be garbage or a footer).
          - The trailing sentinel byte (0x8A) is discarded.
          - Remaining bytes are pairs ``(dx_byte, y_byte)``:
              - ``dx = dx_byte - 0x5B``  (signed differential x)
              - ``x(n) = x(n-1) - dx``   (running accumulator, starts at 0)
              - ``y``  is absolute.
              +
          - If any x-coordinate is negative after decoding, all x values are
            shifted so that ``min(x) == 0``.

        Args:
            raw_bytes (bytes | bytearray): Payload returned by load_card_slot().

        Returns:
            list[tuple[int, int]]: Decoded ``[(x, y), ...]`` stitch coordinates.

        Raises:
            MachineCommError: If the payload (after stripping sentinels) has an
                odd number of bytes.
        """
        data = bytearray(raw_bytes)

        # Leading sentinel must be 0x80
        if not data or data[0] != 0x80:
            raise MachineCommError(
                _tr("9mm card slot payload does not start with expected 0x80 sentinel.")
            )
        data = data[1:]

        # The 5th byte from the end must be 0x8A (end-of-pattern marker);
        # drop it and the 4 trailing footer/padding (?) bytes.
        if len(data) < 5 or data[-5] != 0x8A:
            raise MachineCommError(
                _tr("9mm card slot payload missing expected 0x8A sentinel at position -5.")
            )
        data = data[:-5]

        # # Strip trailing sentinel
        # if data and data[-1] in (0x80, 0x8A):
        #     data = data[:-1]

        if len(data) % 2 != 0:
            raise MachineCommError(
                _tr("9mm card slot payload has odd byte count ({0}) after stripping sentinels.").format(
                    len(data))
            )

        points = []
        x = 0
        for i in range(0, len(data), 2):
            dx = data[i] - 0x5B
            y  = data[i + 1]
            x  = x - dx
            points.append((x, y))

        # Shift so that min(x) == 0 if any coordinate went negative
        if points:
            min_x = min(px for px, _ in points)
            if min_x < 0:
                points = [(px - min_x, py) for px, py in points]

        return points

    @staticmethod
    def decode_card_pattern_maxi(raw_bytes):
        """Decode raw bytes from a MAXI card pattern into stitch coordinates.

        Format:
          - Leading sentinel byte (0x80) is discarded.
          - Last five bytes are discarded (they appear to be garbage or a footer).
          - The trailing sentinel byte (0x8A) is discarded.
          - Remaining bytes are triplets ``(dt_byte, dx_byte, y_byte)``:
              - ``dy_acc += dt_byte - 0xC6``  (accumulates side-transport offset)
              - ``dx = dx_byte - 0x5B``       (signed differential x)
              - ``x(n) = x(n-1) - dx``        (running accumulator, starts at 0)
              - ``y = y_byte + dy_acc``        (absolute base + accumulated offset)
          - If any x-coordinate is negative, all x values are shifted so
            that ``min(x) == 0``.
          - If any y-coordinate is negative, all y values are shifted so
            that ``min(y) == 0``.

        Args:
            raw_bytes (bytes | bytearray): Payload returned by load_card_slot().

        Returns:
            list[tuple[int, int]]: Decoded ``[(x, y), ...]`` stitch coordinates.

        Raises:
            MachineCommError: If the payload (after stripping sentinels) is not
                a multiple of 3 bytes.
        """
        data = bytearray(raw_bytes)

        # Leading sentinel must be 0x80
        if not data or data[0] != 0x80:
            raise MachineCommError(
                _tr("MAXI card slot payload does not start with expected 0x80 sentinel.")
            )
        data = data[1:]

        # The 6th byte from the end must be 0x8A (end-of-pattern marker);
        # drop it and the 5 trailing footer/padding (?) bytes.
        if len(data) < 6 or data[-6] != 0x8A:
            raise MachineCommError(
                _tr("MAXI card slot payload missing expected 0x8A sentinel at position -6.")
            )
        data = data[:-6]

        if len(data) % 3 != 0:
            raise MachineCommError(
                _tr("MAXI card slot payload length {0} is not a multiple of 3 after stripping sentinels.").format(
                    len(data))
            )

        points = []
        x = 0
        dy_acc = 0
        for i in range(0, len(data), 3):
            dy_acc += data[i]     - 0xC6
            dx      = data[i + 1] - 0x5B
            y_base  = data[i + 2]
            x = x - dx
            y = y_base + dy_acc
            points.append((x, y))

        if points:
            min_x = min(px for px, _ in points)
            if min_x < 0:
                points = [(px - min_x, py) for px, py in points]

            min_y = min(py for _, py in points)
            if min_y < 0:
                points = [(px, py - min_y) for px, py in points]

        return points

    # ── Embroidery (hoop) card decoding ──

    @staticmethod
    def decode_card_pattern_embroidery(raw_bytes, hoop=None):
        """Decode an Embroidery memory-card payload into canvas coordinates.

        Reads the stitch stream written by
        :meth:`encode_card_pattern_embroidery`: an optional title group, the
        origin group (``80 28 05 00 <Oy LE16> <Ox LE16> 80``), the 16-entry
        thread-colour table, then stitch groups and plain relative steps, ending
        with ``80 2F 80``.

        Positions are distances from the origin towards the bottom-left in
        payload units (~1/10 mm), so a point is
        ``(origin - V) / EMBROIDERY_SCALE`` in canvas units.  The design is then
        centred in its hoop, which is where the machine's own header margins put
        it; the fractional part is kept because embroidery resolution is finer
        than one canvas unit.

        A colour change is stored as a block of its own (jump marker, resync,
        tie-off, colour stitch), so the decoded point list repeats the change
        position three times — those are real stitches on the card, exactly as
        the machine sews them.

        Args:
            raw_bytes (bytes): Pattern payload returned by
                :meth:`load_card_slot` for an ``'Embroidery'`` slot.
            hoop (str | None): ``'small hoop'`` or ``'large hoop'`` from the
                card preview descriptor.  When it is missing or unknown the
                smallest hoop that holds the design is used.

        Returns:
            dict: {
                'points':      [(x, y), ...] canvas units,
                'elements':    [(ELEM_STITCH, x, y) | (ELEM_COLOR, index), ...],
                'colors':      [(r, g, b), ...] the full 16-entry palette; the
                               payload's own table when it has one, otherwise
                               :attr:`DEFAULT_CARD_COLORS`,
                'stitch_type': 'small hoop' | 'large hoop',
            }

        Raises:
            MachineCommError: If the payload holds no stitch the decoder
                understands.
        """
        data = bytes(raw_bytes)
        x = 0
        y = 0
        colour = 1                  # thread colour number in effect (1..16)
        values = []                 # payload-space positions, as carried
        colours_of_point = []
        origin = None
        colour_table = []

        index = 0
        while index < len(data):
            if data[index] != MachineComm.EMBROIDERY_GROUP_MARK:
                # Plain relative step: two signed bytes (dy, dx), one stitch.
                # Only checked at step boundaries, so an 0x80 inside a delta
                # byte is not mistaken for a group.
                if index + 2 > len(data):
                    break
                y += _s8(data[index])
                x += _s8(data[index + 1])
                values.append((x, y))
                colours_of_point.append(colour)
                index += 2
                continue

            parsed = MachineComm._parse_embroidery_group(data, index)
            if parsed is None:
                # Unknown or truncated group: stop rather than invent stitches.
                break
            commands, index = parsed

            group_colour = None
            group_points = []
            end_of_design = False
            for marker, payload in commands:
                if marker == MachineComm.EMBROIDERY_CMD_ABS_POS:
                    y = _u16be_at(payload, 0)
                    x = _u16be_at(payload, 2)
                    group_points.append((x, y))
                elif marker == MachineComm.EMBROIDERY_CMD_REL_JUMP:
                    y += _s8(payload[0])
                    x += _s8(payload[1])
                    group_points.append((x, y))
                elif (MachineComm.EMBROIDERY_CMD_COLOUR_MIN <= marker
                      <= MachineComm.EMBROIDERY_CMD_COLOUR_MAX):
                    group_colour = marker - 0x2F
                elif marker == MachineComm.EMBROIDERY_CMD_EXTRA:
                    if (len(payload) >= 5
                            and payload[0] == MachineComm.EMBROIDERY_EXTRA_ORIGIN):
                        origin = (int.from_bytes(payload[3:5], "little"),
                                  int.from_bytes(payload[1:3], "little"))
                    elif (len(payload) >= 65
                          and payload[0] == MachineComm.EMBROIDERY_EXTRA_COLOUR_TABLE):
                        entries = payload[1:65]
                        colour_table = [tuple(entries[4 * i:4 * i + 3])
                                        for i in range(16)]
                elif marker == MachineComm.EMBROIDERY_CMD_END:
                    end_of_design = True

            # A colour command covers the stitches of its own group: the short
            # form "80 2E P 3n 80" already places the first point of the new
            # colour in the group, the long form has a group of its own.
            if group_colour is not None:
                colour = group_colour
            for point in group_points:
                values.append(point)
                colours_of_point.append(colour)
            if end_of_design:
                break

        if not values:
            raise MachineCommError(
                _tr("The card payload contains no embroidery stitches."))

        # Without an origin group the values carry no reference point; using the
        # extent maximum keeps the design at least unmirrored.
        if origin is None:
            origin = (max(value[0] for value in values),
                      max(value[1] for value in values))

        scale = MachineComm.EMBROIDERY_SCALE
        points = [((origin[0] - vx) / scale, (origin[1] - vy) / scale)
                  for vx, vy in values]

        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        if hoop not in MachineComm.EMBROIDERY_HOOPS:
            # Missing or unrecognised hoop byte: derive it from the design size.
            hoop = MachineComm._embroidery_hoop_for(points)
        canvas_w, canvas_h = StitchPattern.CANVAS_SIZES[hoop]

        offset_x = (canvas_w - (max(xs) - min(xs))) / 2.0 - min(xs)
        offset_y = (canvas_h - (max(ys) - min(ys))) / 2.0 - min(ys)
        points = [(px + offset_x, py + offset_y) for px, py in points]

        elements = []
        previous_colour = 1
        for (px, py), number in zip(points, colours_of_point):
            if number != previous_colour:
                # ELEM_COLOR carries the palette index, i.e. the number - 1.
                elements.append((ELEM_COLOR, number - 1))
                previous_colour = number
            elements.append((ELEM_STITCH, px, py))

        # The colour numbers index the machine's 16 threads, so the palette is
        # always 16 entries: the payload's table keeps all of it (PC-Designer
        # pads short palettes with black), and without a table the stand-in
        # palette shows which thread each section follows.
        colors = list(colour_table) if colour_table else list(
            MachineComm.DEFAULT_CARD_COLORS)

        return {
            'points': points,
            'elements': elements,
            'colors': colors,
            'stitch_type': hoop,
        }

    @staticmethod
    def _parse_embroidery_group(data, index):
        """Parse one ``0x80 … 0x80`` command group that starts at *index*.

        Returns ``(commands, next_index)`` where each command is
        ``(marker, payload)`` and *next_index* points just past the closing
        ``0x80``.  Returns ``None`` when the group is truncated or holds a
        command the decoder does not know, so the caller stops instead of
        inventing stitches.
        """
        index += 1                  # skip the opening 0x80
        commands = []
        while index < len(data):
            marker = data[index]
            if marker == MachineComm.EMBROIDERY_GROUP_MARK:
                return commands, index + 1
            if marker == MachineComm.EMBROIDERY_CMD_ABS_POS:
                if index + 5 > len(data):
                    return None
                commands.append((marker, data[index + 1:index + 5]))
                index += 5
            elif marker == MachineComm.EMBROIDERY_CMD_REL_JUMP:
                if index + 3 > len(data):
                    return None
                commands.append((marker, data[index + 1:index + 3]))
                index += 3
            elif marker in (MachineComm.EMBROIDERY_CMD_TITLE,
                            MachineComm.EMBROIDERY_CMD_EXTRA):
                if index + 2 > len(data):
                    return None
                end = index + 2 + data[index + 1]
                if end > len(data):
                    return None
                commands.append((marker, data[index + 2:end]))
                index = end
            elif marker in MachineComm.EMBROIDERY_CMD_EXTRA16:
                if index + 3 > len(data):
                    return None
                end = index + 3 + int.from_bytes(data[index + 1:index + 3], "big")
                if end > len(data):
                    return None
                commands.append((marker, data[index + 3:end]))
                index = end
            elif (MachineComm.EMBROIDERY_CMD_COLOUR_MIN <= marker
                  <= MachineComm.EMBROIDERY_CMD_COLOUR_MAX
                  or marker == MachineComm.EMBROIDERY_CMD_END):
                commands.append((marker, b""))
                index += 1
            elif marker in MachineComm.EMBROIDERY_IGNORED_COMMANDS:
                index += 1
            else:
                return None
        return None                 # the group never closed

    @staticmethod
    def _parse_card_preview_descriptor(descriptor):
        """Split the descriptor of the first KB chunk into its logical fields.

        The four bytes mirror the slot's KN header: colour count, 16-bit colour
        mask (big-endian) and the hoop byte.  The count is the number of thread
        colours the design uses, which the machine derives from the header's
        preview-block count by dropping the composite block (so a single-colour
        design and a 9 mm / MAXI pattern both report 1).  The hoop byte is 1 for
        the small and 2 for the large hoop, and 0 for designs without a hoop.

        Args:
            descriptor (bytes): The four descriptor bytes of the first chunk.

        Returns:
            tuple: ``(colour_count, colour_mask, hoop_type)``; *hoop_type* is
            ``'small hoop'``, ``'large hoop'`` or ``None`` when the byte names
            no hoop.
        """
        return (
            descriptor[0],
            int.from_bytes(descriptor[1:3], "big"),
            MachineComm._embroidery_hoop_type(descriptor[3]),
        )

    @staticmethod
    def _embroidery_hoop_type(hoop_byte):
        """Return the stitch type for a header hoop byte, or ``None`` if unset."""
        for stitch_type, entry in MachineComm.EMBROIDERY_HOOPS.items():
            if entry[0] == hoop_byte:
                return stitch_type
        return None

    @staticmethod
    def _embroidery_hoop_for(points):
        """Return the smallest hoop canvas that holds *points*.

        A design that exceeds every hoop falls back to the largest one; the
        machine refuses such writes, so this only affects third-party cards.
        """
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        span_x = max(xs) - min(xs)
        span_y = max(ys) - min(ys)
        for stitch_type in ("small hoop", "large hoop"):
            canvas_w, canvas_h = StitchPattern.CANVAS_SIZES[stitch_type]
            if span_x <= canvas_w and span_y <= canvas_h:
                return stitch_type
        return "large hoop"

    @staticmethod
    def encode_card_preview(pattern):
        """Generate a column-major 1-bit-per-pixel preview image for memory card.

        Draws line segments between consecutive stitch points, scaled and
        centred within the preview dimensions declared in
        :attr:`PREVIEW_SIZES`:

        =========== ====== ======
        Stitch type Height Width
        =========== ====== ======
        9mm           24     53
        MAXI          48     53
        Embroidery    48     48
        =========== ====== ======

        Bit-stream layout (matches the decoding in CardMemoryDialog._build_pixmap):

        * Data is column-major (all bytes for column 0, then column 1, …).
        * Each column occupies ``col_height // 8`` bytes, ordered bottom-to-top
          (byte 0 = bottom-most 8-row group, last byte = top-most group).
        * Within each byte bit 7 (MSB) is the top-most pixel of that 8-row
          group; bit 0 (LSB) is the bottom-most.
        * Embroidery (hoop) designs are delegated to
          :meth:`encode_card_previews_embroidery`, which returns one block per
          colour; this method returns only the composite block for them.

        Args:
            pattern: StitchPattern instance.

        Returns:
            bytes: Encoded preview bitmap.
                   Size: ``img_w * img_h / 8`` bytes.

        Raises:
            MachineCommError: If ``pattern.stitch_type`` has no declared
                preview geometry.
        """
        if pattern.stitch_type in MachineComm.EMBROIDERY_HOOPS:
            # Hoop designs need one block per colour; this helper keeps its
            # single-block contract by returning the composite block.
            return MachineComm.encode_card_previews_embroidery(pattern)[0]

        if pattern.stitch_type not in MachineComm.PREVIEW_SIZES:
            raise MachineCommError(
                _tr("Unknown pattern type: {0}").format(repr(pattern.stitch_type)))

        img_h, img_w = MachineComm.PREVIEW_SIZES[pattern.stitch_type]
        bytes_per_col = img_h // 8
        blank = bytes(img_w * bytes_per_col)

        points = [
            (e[1], e[2])
            for e in pattern.rounded_display_elements()
            if elem_has_coords(e)
        ]
        if len(points) < 2:
            return blank

        xs = [x for x, y in points]
        ys = [y for x, y in points]
        min_x, min_y = min(xs), min(ys)
        span_x = max(xs) - min_x
        span_y = max(ys) - min_y

        # Scale so the pattern fills the image with 1-px margin on each side
        avail_w = img_w - 2
        avail_h = img_h - 2

        if span_x == 0 and span_y == 0:
            scale = 1.0
        elif span_x == 0:
            scale = avail_h / span_y
        elif span_y == 0:
            scale = avail_w / span_x
        else:
            scale = min(avail_w / span_x, avail_h / span_y)

        scaled_w = span_x * scale
        scaled_h = span_y * scale
        off_x = (img_w - scaled_w) / 2.0 - min_x * scale
        off_y = (img_h - scaled_h) / 2.0 - min_y * scale

        # Flat row-major bitmap; index = y * img_w + x; 1 = black, 0 = white
        bitmap = bytearray(img_w * img_h)

        def _draw_line(x0f, y0f, x1f, y1f):
            """Bresenham line from float coords, clipped to bitmap bounds."""
            x0, y0 = round(x0f), round(y0f)
            x1, y1 = round(x1f), round(y1f)
            dx = abs(x1 - x0)
            dy = abs(y1 - y0)
            sx = 1 if x0 < x1 else -1
            sy = 1 if y0 < y1 else -1
            err = dx - dy
            while True:
                if 0 <= x0 < img_w and 0 <= y0 < img_h:
                    bitmap[y0 * img_w + x0] = 1
                if x0 == x1 and y0 == y1:
                    break
                e2 = 2 * err
                if e2 > -dy:
                    err -= dy
                    x0 += sx
                if e2 < dx:
                    err += dx
                    y0 += sy

        for i in range(len(points) - 1):
            px0, py0 = points[i]
            px1, py1 = points[i + 1]
            _draw_line(
                px0 * scale + off_x, py0 * scale + off_y,
                px1 * scale + off_x, py1 * scale + off_y,
            )

        # Mirror upside-down
        mirrored = bytearray(img_w * img_h)
        for y in range(img_h):
            for x in range(img_w):
                mirrored[(img_h - 1 - y) * img_w + x] = bitmap[y * img_w + x]
        bitmap = mirrored

        # Encode: column-major, groups of 8 rows from bottom to top,
        # MSB = top-most pixel within each 8-row group.
        result = bytearray(img_w * bytes_per_col)
        for col in range(img_w):
            for byte_idx in range(bytes_per_col):
                y_base = img_h - 8 - byte_idx * 8   # bottom group first
                byte_val = 0
                for bit in range(8):
                    if bitmap[(y_base + bit) * img_w + col]:
                        byte_val |= 1 << (7 - bit)  # MSB = topmost pixel
                result[col * bytes_per_col + byte_idx] = byte_val
        return bytes(result)

    @staticmethod
    def encode_card_header(pattern, card_no_bytes, preview_bytes, pattern_bytes, name_bytes, points=None):
        """Build the 30-byte KN header for 9mm / MAXI card writes.

        Embroidery (hoop) designs use :meth:`encode_card_header_embroidery`;
        the caller picks the method from ``pattern.stitch_type``.
        """
        stitch_type = pattern.stitch_type

        if points is None:
            points = [(e[1], e[2]) for e in pattern.rounded_display_elements() if elem_has_coords(e)]
        if not points:
            raise MachineCommError(_tr("Cannot encode header for an empty pattern."))

        xs = [x for x, y in points]
        ys = [y for x, y in points]
        dxs = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]

        y_min         = min(ys)
        y_max         = max(ys)
        y_min_neg     = -min(ys)
        dy_0n         = ys[-1] - ys[0]
        dx_abs_max    = max((abs(dx) for dx in dxs), default=0)
        d0x_min_abs   = abs(min(xs) - xs[0])
        pn_x          = xs[-1]
        span_x        = max(xs) - min(xs)
        span_y        = max(ys) - min(ys)

        # Calculate y_min_symmetry
        # It is based on y_min but with additional flag (MSB set) if the pattern is "top-heavy" (y_max farther from 27 than y_min)
        # if pattern is "symmetrical" (i.e. y_max and y_min are equally distant from 27, or y_max=53 and y_min=0), then y_min_symmetry is 0 (with no extra flag)
        if stitch_type == '9mm':
            y_min_symmetry = y_min
            if y_max - 27 > 27 - y_min: # Check if top-heavy
                y_min_symmetry |= 0x80  # set MSB to indicate top-heavy
                
                if y_max >= 27 and y_min <= 27: # If pattern is symmetrical, set y_min_symmetry to 0 (no extra flag)
                    dymax_27 = y_max - 27
                    dymin_27 = 27 - y_min
                    is_max_width = y_max == 53 and y_min == 0

                    if dymax_27 == dymin_27 or is_max_width:
                        y_min_symmetry = 0
        else:
            y_min_symmetry = 0  # Not used for MAXI or Embroidery, set to 0

        
        size_preview = len(preview_bytes)
        size_pattern = len(pattern_bytes)
        size_name    = len(name_bytes)   # includes null terminator

        unknown_1 = 0x10 # allows longitudinal scaling in machine menu; 0x10 seems to give much freedom

        type_byte_map = {'9mm': 0x01, 'MAXI': 0x02, 'Embroidery': 0x03}
        if stitch_type not in type_byte_map:
            raise MachineCommError(_tr("Unknown pattern type: {0}").format(repr(stitch_type)))

        type_byte = type_byte_map[stitch_type]
        # bank_byte = 0xD0 if stitch_type == 'MAXI' else 0xC0
        bank_byte = 0xC0 # Not sure why but PCD sends here 0xC0 for both 9mm and MAXI patterns, even though it uses 0xD0 for MAXI in the load/delete commands

        def _pack2(v):
            return (v & 0xFFFF).to_bytes(2, 'big')
        
        if pattern.stitch_type == "9mm":
            y_min_to_bound = 0x36 - min(ys)
        elif pattern.stitch_type == "MAXI":
            ys_norm_27 = [y - ys[0] + 27 for y in ys]
            y_min_to_bound = 0x36 - min(ys_norm_27)
        else:
            raise MachineCommError(
                _tr("Unsupported stitch type for machine encoding: {0}").format(repr(pattern.stitch_type))
            )

        return (
            bytes([0x00, 0x00]) +
            card_no_bytes +
            bytes([bank_byte, 0x00, type_byte]) +
            _pack2(d0x_min_abs) +
            _pack2(pn_x) +
            _pack2(span_x) +
            _pack2(y_min_to_bound) +
            _pack2(span_y) +
            (_pack2(dy_0n) if stitch_type == "MAXI" else _pack2(0)) +
            bytes([unknown_1]) +
            (_pack2(y_min_neg) if stitch_type == "MAXI" else _pack2(0)) +
            bytes([y_min_symmetry] if stitch_type == "9mm" else bytes([0])) +
            bytes([dx_abs_max]) +
            _pack2(size_preview) +
            bytes([0x01]) +
            _pack2(size_pattern) +
            bytes([size_name])
        )

    @staticmethod
    def encode_card_pattern_9mm(pattern):
        """Encode a 9mm stitch pattern into the memory card byte format.

        Each stitch point produces two bytes:

        * ``b[0] = (x(n-1) - x(n)) + 0x5B``  — differential x with 0x5B bias.
          The virtual previous-x for the first stitch is 0.
        * ``b[1] = y(n)``                      — absolute y.

        The byte pattern is wrapped with a leading ``0x80`` sentinel and a trailing
        ``0x8A`` sentinel.

        Args:
            pattern: StitchPattern instance (``stitch_type`` must be ``'9mm'``).

        Returns:
            bytes: ``N×2 payload bytes``.
            list: Translated (x, y) coordinates.

        Raises:
            MachineCommError: If any differential-x value is outside the open
                interval ``(-37, 37)``, i.e. ``|dx| >= 37``.  The error message
                names the offending stitch and advises inserting intermediate
                stitches.
        """
        points = [
            (e[1], e[2])
            for e in pattern.rounded_display_elements()
            if elem_has_coords(e)
        ]
        if not points:
            return bytes([0x80, 0x8A]), []  # Empty pattern with leading sentinel
        
        # # Make x_min = 0
        # x_min = min(x for x, y in points)
        # translated_xy = [(x - x_min, y) for x, y in points]

        translated_xy = MachineComm._translate_9mm_points(points)

        encoded = bytearray([0x80])
        x_prev = translated_xy[0][0]
        for i, (x, y) in enumerate(translated_xy):
            dx = x_prev - x
            if not (-37 < dx < 37): # dx byte is not allowed to be 0x80 or above
                raise MachineCommError(
                    _tr("The distance between consecutive stitch points is too large.")
                    + "\n" +
                    _tr("Please insert intermediate stitches and try again.")
                )
            encoded.append(dx + 0x5B)
            encoded.append(y & 0xFF)
            x_prev = x

        encoded.append(0x8A)
        return bytes(encoded), translated_xy

    @staticmethod
    def encode_card_pattern_maxi(pattern):
        """Encode a MAXI stitch pattern into the memory card byte format.

        Calls :meth:`_translate_maxi_points` to convert pattern coordinates into
        ``(x, stored_y, transport_delta)`` triplets, then encodes each stitch as
        three bytes:

        * ``b[0] = transport_delta + 0xC6``  — differential side-transport with
          0xC6 bias.
        * ``b[1] = (x(n-1) - x(n)) + 0x5B`` — differential x with 0x5B bias.
          The virtual previous-x for the first stitch is 0.
        * ``b[2] = stored_y``                — absolute (transport-adjusted) y.

        The byte pattern is wrapped with a leading ``0x80`` sentinel and a trailing
        ``0x8A`` sentinel.

        Args:
            pattern: StitchPattern instance (``stitch_type`` must be ``'MAXI'``).

        Returns:
            bytes: ``N×3 payload bytes``.
            list: Translated (x, y) coordinates.

        Raises:
            MachineCommError: If any differential-x value is outside the open
                interval ``(-37, 37)``, i.e. ``|dx| >= 37``.  The error message
                names the offending stitch and advises inserting intermediate stitches.
        """
        raw_elems = [e for e in pattern.rounded_display_elements() if elem_has_coords(e)]
        if not raw_elems:
            return bytes([0x80, 0x8A]), []  # Empty pattern with leading sentinel

        translated_xyt, translated_xy = MachineComm._translate_maxi_points(raw_elems)

        encoded = bytearray([0x80])
        x_prev = translated_xyt[0][0]  # x of the first stitch
        for i, (x, stored_y, delta) in enumerate(translated_xyt):
            dx = x_prev - x
            if not (-37 < dx < 37): # dx byte is not allowed to be 0x80 or above
                raise MachineCommError(
                    _tr("The distance between consecutive stitch points is too large.")
                    + "\n" +
                    _tr("Please insert intermediate stitches and try again.")
                )
            encoded.append((delta + 0xC6) & 0xFF)
            encoded.append((dx    + 0x5B) & 0xFF)
            encoded.append(stored_y & 0xFF)
            x_prev = x

        encoded.append(0x8A)
        return bytes(encoded), translated_xy

    # ── Embroidery (hoop) card encoding ──

    @staticmethod
    def _embroidery_design(pattern):
        """Return ``(points, sections)`` for a hoop pattern.

        ``points`` are ``(x, y)`` canvas-unit coordinates with the fractional
        part intact — embroidery files carry roughly 1/10 mm resolution, so the
        rounding done by the 9mm/MAXI encoders would visibly coarsen the design.
        ``sections`` is ``[(first_point_index, colour_number), ...]`` with
        1-based machine colour numbers (1..16); the first entry starts at 0.

        A colour change with no stitch after it (an ``ELEM_COLOR`` at the end of
        the element list) is ignored: the card format cannot represent it, and
        PC-Designer never writes one.

        Raises:
            MachineCommError: If the design has no stitches or a colour index
                lies outside the machine's 16-colour palette.
        """
        points = []
        sections = [(0, 1)]
        for elem in pattern.elements:
            if elem[0] == ELEM_COLOR:
                number = int(elem[1]) + 1
                if not 1 <= number <= 16:
                    raise MachineCommError(_tr(
                        "Colour index {0} is outside the machine's 16-colour palette."
                    ).format(int(elem[1])))
                if not points:
                    # Colour changes before the first stitch select the
                    # initial thread colour.
                    sections[0] = (0, number)
                elif number != sections[-1][1]:
                    sections.append((len(points), number))
            elif elem_has_coords(elem):
                points.append((float(elem[1]), float(elem[2])))

        if not points:
            raise MachineCommError(_tr("The embroidery design contains no stitches."))

        # Entries recorded after the last stitch can never be sewn.
        last_index = len(points) - 1
        sections = [entry for entry in sections if entry[0] <= last_index]
        return points, sections

    @staticmethod
    def _embroidery_values(points):
        """Convert canvas-unit points to the card's distance-from-maximum values.

        Returns ``(values, origin)``.  Every payload value is measured from the
        design's axis maximum, so both components decrease as the canvas
        coordinates increase; ``origin`` is that corner in payload units.
        ``int()`` truncates toward zero, which is what the format uses.
        """
        scale = MachineComm.EMBROIDERY_SCALE
        max_x = max(point[0] for point in points)
        max_y = max(point[1] for point in points)
        origin = (int(scale * max_x), int(scale * max_y))
        values = [
            (int(scale * (max_x - x)), int(scale * (max_y - y)))
            for x, y in points
        ]
        return values, origin

    @staticmethod
    def _embroidery_colour_mask(colour_numbers):
        """Return the 16-bit palette mask for 1-based machine colour *numbers*.

        Bit *n-1* marks colour *n*; only colours actually used are set.
        (PC-Designer sometimes writes 0x0080 / 0x0082 for one- and two-colour
        designs; that is a bug in the original software and is not reproduced.)
        """
        mask = 0
        for number in colour_numbers:
            mask |= 1 << (number - 1)
        return mask

    @staticmethod
    def _embroidery_colour_of_point(sections, point_count):
        """Return the 1-based colour number of each point, derived from *sections*."""
        colours = [sections[0][1]] * point_count
        for position in range(1, len(sections)):
            start = sections[position][0]
            end = (sections[position + 1][0]
                   if position + 1 < len(sections) else point_count)
            for index in range(start, end):
                colours[index] = sections[position][1]
        return colours

    @staticmethod
    def _build_embroidery_payload(values, sections, origin, colours=None,
                                  trailing_tieoffs=False):
        """Build the Embroidery stitch stream from payload-space *values*.

        Args:
            values: ``[(vx, vy), ...]`` distances from the design's top-right
                corner, as produced by :meth:`_embroidery_values`.
            sections: ``[(first_point_index, colour_number), ...]``.
            origin: ``(Ox, Oy)`` top-right corner in payload units.  It is sent
                in the leading ``80 28 05`` group; without it the receiver
                treats the distances as plain coordinates and draws the design
                mirrored in both axes.
            colours: 16-entry RGB palette, ``[(r, g, b), ...]``.  Shorter lists
                are padded with black so the ``28 41 01`` table always carries
                16 entries.  ``None`` omits the table.
            trailing_tieoffs: Append one zero-length stitch per colour change
                before the end marker.  PC-Designer card writes do not use
                these, so the default is off.

        The colour-change block *replaces* the ordinary record of the change
        point (jump marker, absolute resync, tie-off, colour stitch).  That is
        what real card slots contain; emitting it after the ordinary record
        would add two bytes per change and does not match the format.
        """
        out = bytearray()

        # Design origin: the corner every position is measured from.  y is
        # written first, both words little-endian (unlike every other field).
        out += bytes([0x80, 0x28, 0x05, 0x00])
        out += _u16le(origin[1]) + _u16le(origin[0])
        out += bytes([0x80])

        # Thread-colour table: subtype 0x01 plus 16 x (R, G, B, pad).
        if colours is not None:
            table = [(int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF)
                     for r, g, b in list(colours)[:16]]
            table += [(0, 0, 0)] * (16 - len(table))
            out += bytes([0x80, 0x28, 0x41, 0x01])
            for r, g, b in table:
                out += bytes([r, g, b, 0x00])
            out += bytes([0x80])

        out += bytes([0x80, 0x2E])
        out += _u16be(values[0][1]) + _u16be(values[0][0])
        out += bytes([0x2F + sections[0][1], 0x80])

        changes = {start: colour for start, colour in sections[1:]}
        tie_offs = 0
        prev = values[0]
        for index in range(1, len(values)):
            cur = values[index]
            if index in changes:
                tie_offs += 1
                out += bytes([0x80, 0x20, 0x80])            # jump marker
                out += bytes([0x80, 0x2E]) + _u16be(cur[1]) + _u16be(cur[0])
                out += bytes([0x80])                        # resync to the change point
                out += bytes([0x00, 0x00])                  # tie-off
                out += bytes([0x80, 0x2E]) + _u16be(cur[1]) + _u16be(cur[0])
                out += bytes([0x2F + changes[index], 0x80])  # new thread colour
            else:
                dy, dx = cur[1] - prev[1], cur[0] - prev[0]
                if -127 <= dy <= 127 and -127 <= dx <= 127:
                    out += bytes([dy & 0xFF, dx & 0xFF])
                else:
                    # Absolute resync.  0x80 can never start a plain record,
                    # because a delta of -128 is never emitted.
                    out += bytes([0x80, 0x2E]) + _u16be(cur[1]) + _u16be(cur[0])
                    out += bytes([0x80])
            prev = cur

        if trailing_tieoffs:
            out += bytes([0x00, 0x00]) * tie_offs
        out += bytes([0x80, 0x2F, 0x80])
        return bytes(out)

    @staticmethod
    def _rasterize_embroidery_preview(values, point_colours, only_colour=None):
        """Pack one embroidery preview block from payload-space *values*.

        ``point_colours`` holds the 1-based colour number of each point.  With
        *only_colour* set, only points of that colour are plotted and a line is
        drawn only between two consecutive points of that colour.

        The block geometry comes from :attr:`PREVIEW_SIZES`.  The stored bitmap
        is the display image rotated by 180°, so a raster pixel ``(px, py)`` in
        payload space goes to column *px*, bit ``py % 8`` of byte ``py // 8``.
        """
        height, width = MachineComm.PREVIEW_SIZES["Embroidery"]
        bytes_per_col = height // 8
        block = bytearray(MachineComm.EMBROIDERY_PREVIEW_SIZE)
        if not values:
            return bytes(block)

        xs = [value[0] for value in values]
        ys = [value[1] for value in values]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span_x, span_y = max_x - min_x, max_y - min_y

        # One pixel margin per side; the same scale on both axes keeps the
        # thumbnail's aspect ratio.
        available_w = float(width - 2)
        available_h = float(height - 2)
        if span_x == 0 and span_y == 0:
            scale = 1.0
        elif span_x == 0:
            scale = available_h / span_y
        elif span_y == 0:
            scale = available_w / span_x
        else:
            scale = min(available_w / span_x, available_h / span_y)

        off_x = (width - span_x * scale) / 2.0 - min_x * scale
        off_y = (height - span_y * scale) / 2.0 - min_y * scale

        def put(px, py):
            if 0 <= px < width and 0 <= py < height:
                block[px * bytes_per_col + py // 8] |= 1 << (py % 8)

        def draw_line(x0, y0, x1, y1):
            dx, dy = abs(x1 - x0), abs(y1 - y0)
            step_x = 1 if x0 < x1 else -1
            step_y = 1 if y0 < y1 else -1
            err = dx - dy
            while True:
                put(x0, y0)
                if x0 == x1 and y0 == y1:
                    break
                err2 = 2 * err
                if err2 > -dy:
                    err -= dy
                    x0 += step_x
                if err2 < dx:
                    err += dx
                    y0 += step_y

        pixels = [
            (round(value[0] * scale + off_x), round(value[1] * scale + off_y))
            for value in values
        ]

        for index, (px, py) in enumerate(pixels):
            if only_colour is None or point_colours[index] == only_colour:
                put(px, py)
        for index in range(1, len(pixels)):
            if only_colour is not None and (
                point_colours[index - 1] != only_colour
                or point_colours[index] != only_colour
            ):
                continue
            draw_line(pixels[index - 1][0], pixels[index - 1][1],
                      pixels[index][0], pixels[index][1])
        return bytes(block)

    @staticmethod
    def encode_card_previews_embroidery(pattern):
        """Return the Embroidery preview blocks for *pattern*.

        Block 0 is the whole design.  With two or more colours, one further
        block per distinct machine colour number follows, in ascending order.
        A single-colour design has only the composite block, matching the
        header's block-count field.
        """
        points, sections = MachineComm._embroidery_design(pattern)
        values, _origin = MachineComm._embroidery_values(points)
        point_colours = MachineComm._embroidery_colour_of_point(sections, len(points))

        blocks = [MachineComm._rasterize_embroidery_preview(values, point_colours)]
        colours = sorted({colour for _start, colour in sections})
        if len(colours) > 1:
            for colour in colours:
                blocks.append(MachineComm._rasterize_embroidery_preview(
                    values, point_colours, only_colour=colour))
        return blocks

    @staticmethod
    def encode_card_pattern_embroidery(pattern):
        """Encode a hoop design into the Embroidery memory-card payload.

        Returns:
            tuple[bytes, list]: ``(payload, points)``; *points* are the canvas
            coordinates the payload was derived from.

        Raises:
            MachineCommError: If the design is empty or a colour index is
                outside the 16-colour palette.  The card-slot size cap is not
                checked here: it applies to the whole transfer payload, so
                :meth:`send_card_slot` enforces it where the previews and the
                filename are known.
        """
        points, sections = MachineComm._embroidery_design(pattern)
        values, origin = MachineComm._embroidery_values(points)
        payload = MachineComm._build_embroidery_payload(
            values, sections, origin,
            colours=pattern.colors if pattern.colors else None,
        )
        return payload, points

    @staticmethod
    def encode_card_header_embroidery(pattern, card_no_bytes, preview_bytes,
                                      pattern_bytes, name_bytes):
        """Build the 30-byte KN header for an Embroidery card write.

        Mirrors :meth:`encode_card_header` for hoop designs; the position fields
        use the distance-from-maximum payload values instead of canvas units.

        Layout (multi-byte fields big-endian)::

            00 00 | card_no[2] | C0 00 03 | Vy0 | Vy_last | span_y
            | Vx0 | span_x | colour mask | hoop | margin_y | margin_x
            | preview size | block count | pattern size | filename length
        """
        stitch_type = pattern.stitch_type
        points, sections = MachineComm._embroidery_design(pattern)
        values, _origin = MachineComm._embroidery_values(points)
        block_count = len(preview_bytes) // MachineComm.EMBROIDERY_PREVIEW_SIZE
        pattern_size = len(pattern_bytes)
        filename_len = len(name_bytes)

        hoop_byte, hoop_width, hoop_height = MachineComm.EMBROIDERY_HOOPS[stitch_type]
        vx = [value[0] for value in values]
        vy = [value[1] for value in values]
        span_x = max(vx) - min(vx)
        span_y = max(vy) - min(vy)
        if span_x > hoop_width or span_y > hoop_height:
            raise MachineCommError(_tr(
                "The embroidery design is larger than the selected hoop."
            ))
        if pattern_size > 0xFFFF:
            raise MachineCommError(_tr(
                "The encoded embroidery pattern is {0} bytes, but the memory card "
                "format can hold at most 65535."
            ).format(pattern_size))

        colours = sorted({colour for _start, colour in sections})
        expected_blocks = 1 if len(colours) == 1 else len(colours) + 1
        if block_count != expected_blocks:
            # The receiver derives the expected transfer size from this field;
            # a wrong value makes it wait forever instead of committing.
            raise MachineCommError(_tr(
                "Internal error: {0} preview block(s) generated, but the header "
                "expects {1}."
            ).format(block_count, expected_blocks))

        return (
            bytes([0x00, 0x00]) + bytes(card_no_bytes) +
            bytes([0xC0, 0x00, 0x03]) +
            _u16be(vy[0]) + _u16be(vy[-1]) + _u16be(span_y) +
            _u16be(vx[0]) + _u16be(span_x) +
            _u16be(MachineComm._embroidery_colour_mask(colours)) +
            bytes([hoop_byte]) +
            _u16be((hoop_height - span_y) // 2) +
            _u16be((hoop_width - span_x) // 2) +
            _u16be(MachineComm.EMBROIDERY_PREVIEW_SIZE) +
            bytes([block_count]) + _u16be(pattern_size) + bytes([filename_len])
        )

    # ── P-Memory decoding/encoding ──

    @staticmethod
    def decode_pmemory_index(raw_bytes, machine_model):
        """Decode raw P-Memory query response bytes into a structured dict.

        The expected layout of raw_bytes (as returned by query_pmemory_index) is:
            <payload ASCII chars> + CTRL_ETB + <2 ASCII-hex checksum chars>

        Payload structure (all values ASCII-hex encoded):
            [0:2]                     - number of slots (1 byte, hex)
            repeated num_slots times:
              [0:2]                 - stitch type (0x00 = 9mm, 0x01 = MAXI)
              [2:6]                 - number of stitches (2 bytes, hex)
              [6:10]                - unknown (4 chars, ignored)
              [6:14]                - unknown (8 chars, ignored) - only for PFAFF Creative 1475 CD, other models have only 4 chars total for the unknown field
            last 4 chars            - total free memory (2 bytes, unsigned hex)

        Args:
            raw_bytes (bytes): Raw bytes returned by query_pmemory_index().
            machine_model (str): Machine model string from configuration

        Returns:
            dict: {
                'num_slots':   int,
                'free_memory': int,
                'slots': [{'type': '9mm' | 'MAXI', 'size': int}, ...],
            }

        Raises:
            MachineCommError: If the checksum is wrong or the payload is malformed.
        """
        if len(raw_bytes) < 3:
            raise MachineCommError(_tr("P-Memory response too short."))

        checksum_ascii = raw_bytes[-2:]
        etb_byte = raw_bytes[-3]
        payload = raw_bytes[:-3]

        if etb_byte != MachineComm.CTRL_ETB:
            raise MachineCommError(
                _tr("Expected CTRL_ETB at position -3, got 0x{0:02X}.").format(etb_byte)
            )

        # Verify checksum
        try:
            received_checksum = int(checksum_ascii.decode('ascii'), 16)
        except (ValueError, UnicodeDecodeError) as exc:
            raise MachineCommError(
                _tr("Invalid checksum encoding: {0}").format(repr(checksum_ascii))
            ) from exc

        expected_checksum = MachineComm.checksum(payload)
        if received_checksum != expected_checksum:
            raise MachineCommError(
                _tr("P-Memory checksum mismatch: expected {0:02X}, got {1:02X}").format(
                    expected_checksum, received_checksum)
            )

        # Decode payload as ASCII
        try:
            text = payload.decode('ascii')
        except UnicodeDecodeError as exc:
            raise MachineCommError(_tr("P-Memory payload is not valid ASCII.")) from exc

        if len(text) < 2:
            raise MachineCommError(_tr("P-Memory payload too short to read slot count."))

        try:
            num_slots = int(text[0:2], 16)
        except ValueError as exc:
            raise MachineCommError(
                _tr("Invalid slot count encoding: {0}").format(repr(text[0:2]))
            ) from exc

        # The 1475 CD sends four extra unknown characters per slot.
        model = MachineComm._require_model(machine_model)
        bytes_per_slot = 14 if model.pmemory_variant == PMEMORY_VARIANT_1475CD else 10

        expected_len = 2 + num_slots * bytes_per_slot + 4
        if len(text) != expected_len:
            raise MachineCommError(
                _tr("P-Memory payload length mismatch: expected {0} chars, got {1}.").format(
                    expected_len, len(text))
            )

        slots = []
        offset = 2
        for i in range(num_slots):
            slot_text = text[offset:offset + bytes_per_slot]
            try:
                type_byte = int(slot_text[0:2], 16)
                size = int(slot_text[2:6], 16)
                # slot_text[6:10] - purpose unknown, ignored
                # slot_text[6:14] - purpose unknown, ignored; 1475CD has 4 extra chars here compared to other models
            except ValueError as exc:
                raise MachineCommError(
                    _tr("Invalid data for slot {0}: {1}").format(i + 1, repr(slot_text))
                ) from exc

            if size == 0:
                stitch_type = None  # Empty slot
            elif type_byte == 0x00:
                stitch_type = "9mm"
            elif type_byte == 0x01:
                stitch_type = "MAXI"
            else:
                stitch_type = f"unknown(0x{type_byte:02X})"

            slots.append({'type': stitch_type, 'size': size})
            offset += bytes_per_slot

        try:
            free_memory = int(text[offset:offset + 4], 16)
        except ValueError as exc:
            raise MachineCommError(
                _tr("Invalid free memory encoding: {0}").format(repr(text[offset:offset + 4]))
            ) from exc

        # Original SW shows 1 byte less than machine reports on 75xx models, reason unknown.
        # The 1475 CD does not need this adjustment.
        adjusted_free = (
            free_memory if model.pmemory_variant == PMEMORY_VARIANT_1475CD
            else free_memory - 1
        )

        return {
            'num_slots': num_slots,
            'free_memory': adjusted_free,
            'slots': slots,
        }

    @staticmethod
    def decode_pmemory_pattern(data, slot_type):
        """Decode a raw hex-ASCII pattern payload received from the machine.

        9mm format  - repeating groups of 5 ASCII-hex chars:
            XXX (3 hex digits, x coordinate)
            YY  (2 hex digits, y coordinate)

        MAXI format - repeating groups of 7 ASCII-hex chars:
            XXX (3 hex digits, raw x coordinate)
            YY  (2 hex digits, y coordinate)
            S   (sign character: '+' or '-')
            T   (1 hex digit, side-transport delta)

        For MAXI, a running ``maxi_transport`` accumulator is updated by
        ``+T`` or ``-T`` for each stitch, and the effective x coordinate is
        ``raw_x + maxi_transport``.

        Args:
            data (bytes): Concatenated hex-ASCII payload returned by
                load_pmemory_slot().
            slot_type (str): '9mm' or 'MAXI'.

        Returns:
            list[tuple[int, int]]: List of (x, y) stitch coordinates.

        Raises:
            MachineCommError: If the data length is not a multiple of the
                expected group size, or if any group contains invalid chars.
        """
        try:
            text = data.decode('ascii')
        except UnicodeDecodeError as exc:
            raise MachineCommError(_tr("Pattern data is not valid ASCII.")) from exc

        points = []

        if slot_type == "9mm":
            step = 5
            if len(text) % step != 0:
                raise MachineCommError(
                    _tr("9mm pattern data length {0} is not a multiple of {1}.").format(len(text), step)
                )
            for i in range(0, len(text), step):
                group = text[i:i + step]
                try:
                    x = int(group[0:3], 10)
                    y = int(group[3:5], 10)
                except ValueError as exc:
                    raise MachineCommError(
                        _tr("Invalid 9mm stitch data at offset {0}: {1}").format(i, repr(group))
                    ) from exc
                points.append((x, y))

        elif slot_type == "MAXI":
            step = 7
            if len(text) % step != 0:
                raise MachineCommError(
                    _tr("MAXI pattern data length {0} is not a multiple of {1}.").format(len(text), step)
                )
            maxi_transport = 0
            for i in range(0, len(text), step):
                group = text[i:i + step]
                try:
                    x = int(group[0:3], 10)
                    y = int(group[3:5], 10)
                    sign = group[5]
                    transport_delta = int(group[6], 10)
                except (ValueError, IndexError) as exc:
                    raise MachineCommError(
                        _tr("Invalid MAXI stitch data at offset {0}: {1}").format(i, repr(group))
                    ) from exc
                if sign == '+':
                    maxi_transport += transport_delta
                elif sign == '-':
                    maxi_transport -= transport_delta
                else:
                    raise MachineCommError(
                        _tr("Invalid sign character {0} at offset {1}.").format(repr(sign), i)
                    )
                points.append((x, y + maxi_transport))

        else:
            raise MachineCommError(
                _tr("Unknown slot type for pattern decoding: {0}").format(repr(slot_type))
            )

        return points

    @staticmethod
    def encode_pmemory_header_75xx(pattern, points=None):
        """Encode the fixed header for the given pattern. Valid for Creative 7550/7570.

        Returns ASCII-encoded bytes (no framing, no checksum).
        32 chars (16 bytes) — bytes 0-15.
        Each byte is represented as two uppercase hex digits.

        Args:
            pattern: StitchPattern instance; ``stitch_type`` is always read from here.
            points: Optional list of ``(x, y)`` tuples representing the final
                machine-side coordinates (as returned by
                :meth:`encode_machine_stitch_data`).  When supplied these are
                used directly so the header reflects any transport adjustments or
                inserted intermediate stitches.  When ``None``, coordinates are
                derived from ``pattern.rounded_display_elements()``.

        Raises:
            MachineCommError: If the stitch type is not supported or pattern is empty.
        """
        if points is None:
            points = [(e[1], e[2]) for e in pattern.rounded_display_elements() if elem_has_coords(e)]
        if not points:
            raise MachineCommError(_tr("Cannot encode header for an empty pattern."))
        xs = [x for x, y in points]
        ys = [y for x, y in points]
        dxs = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]

        y_min         = min(ys)
        y_max         = max(ys)
        dy_0n         = ys[-1] - ys[0]
        dx_abs_max    = max((abs(dx) for dx in dxs), default=0)
        d0x_min_abs   = abs(min(xs) - xs[0])
        pn_x          = xs[-1]
        span_x        = max(xs) - min(xs)
        span_y        = max(ys) - min(ys)

        if pattern.stitch_type == "9mm":
            y_min_to_bound = 0x36 - min(ys)
            return (
                f"{y_min          & 0xFF:02X}"   # byte  0   y_min
                f"{y_max          & 0xFF:02X}"   # byte  1   y_max
                f"{dx_abs_max     & 0xFF:02X}"   # byte  2   dx_abs_max
                f"{0              & 0xFF:02X}"   # byte  3
                f"{0              & 0xFF:02X}"   # byte  4
                f"{16             & 0xFF:02X}"   # byte  5   unknown, allows longitudinal scaling
                f"{d0x_min_abs    & 0xFFFF:04X}" # bytes 6-7
                f"{pn_x           & 0xFFFF:04X}" # bytes 8-9
                f"{span_x         & 0xFFFF:04X}" # bytes 10-11
                f"{y_min_to_bound & 0xFFFF:04X}" # bytes 12-13
                f"{span_y         & 0xFFFF:04X}" # bytes 14-15
                f"{0              & 0xFFFF:04X}" # bytes 16-17
            ).encode('ascii')
        elif pattern.stitch_type == "MAXI":
            ys_norm_27 = [y - ys[0] + 27 for y in ys]
            y_min_to_bound = 0x36 - min(ys_norm_27)
            return (
                f"{0              & 0xFF:02X}"   # byte  0   y_min_norm (0, normalised)
                f"{(span_y // 2)  & 0xFF:02X}"   # byte  1   y_max_norm_div_2
                f"{dx_abs_max     & 0xFF:02X}"   # byte  2   dx_abs_max
                f"{0              & 0xFF:02X}"   # byte  3
                f"{0              & 0xFF:02X}"   # byte  4
                f"{16             & 0xFF:02X}"   # byte  5   unknown, allows longitudinal scaling
                f"{d0x_min_abs    & 0xFFFF:04X}" # bytes 6-7
                f"{pn_x           & 0xFFFF:04X}" # bytes 8-9
                f"{span_x         & 0xFFFF:04X}" # bytes 10-11
                f"{y_min_to_bound & 0xFFFF:04X}" # bytes 12-13
                f"{span_y         & 0xFFFF:04X}" # bytes 14-15
                f"{dy_0n          & 0xFFFF:04X}" # bytes 16-17  dy_0n
            ).encode('ascii')
        else:
            raise MachineCommError(
                _tr("Unsupported stitch type for machine encoding: {0}").format(repr(pattern.stitch_type))
            )

    @staticmethod
    def encode_pmemory_header_1475cd(pattern, points=None):
        """Encode the fixed header for the given pattern. Valid for Creative 1475 CD.

        Returns ASCII-encoded bytes (no framing, no checksum).
        16 chars (8 bytes) — bytes 0-7.
        Each byte is represented as two uppercase hex digits.

        Args:
            pattern: StitchPattern instance; ``stitch_type`` is always read from here.
            points: Optional list of ``(x, y)`` tuples representing the final
                machine-side coordinates (as returned by
                :meth:`encode_machine_stitch_data`).  When supplied these are
                used directly so the header reflects any transport adjustments or
                inserted intermediate stitches.  When ``None``, coordinates are
                derived from ``pattern.rounded_display_elements()``.

        Raises:
            MachineCommError: If the stitch type is not supported or pattern is empty.
        """
        if points is None:
            points = [(e[1], e[2]) for e in pattern.rounded_display_elements() if elem_has_coords(e)]
        if not points:
            raise MachineCommError(_tr("Cannot encode header for an empty pattern."))
        xs = [x for x, y in points]
        ys = [y for x, y in points]
        dxs = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]

        y_min         = min(ys)
        y_max         = max(ys)
        dx_abs_max    = max((abs(dx) for dx in dxs), default=0)
        span_y        = max(ys) - min(ys)
        span_x        = max(xs) - min(xs)
        span_x_mm     = span_x // 6

        if pattern.stitch_type == "9mm":
            return (
                f"{y_min          & 0xFF:02X}"   # byte  0   y_min
                f"{y_max          & 0xFF:02X}"   # byte  1   y_max
                f"{dx_abs_max     & 0xFF:02X}"   # byte  2   dx_abs_max
                f"{16             & 0xFF:02X}"   # byte  3   unknown, allows longitudinal scaling
            ).encode('ascii')
        elif pattern.stitch_type == "MAXI":
            return (
                f"{0              & 0xFF:02X}"   # byte  0   y_min_norm (0, normalised)
                f"{span_y         & 0xFF:02X}"   # byte  1   y_max_norm
                f"{span_x_mm      & 0xFF:02X}"   # byte  2   span_x_mm
                f"{16             & 0xFF:02X}"   # byte  3   unknown, allows longitudinal scaling
            ).encode('ascii')
        else:
            raise MachineCommError(
                _tr("Unsupported stitch type for machine encoding: {0}").format(repr(pattern.stitch_type))
            )
        
    @staticmethod
    def encode_pmemory_stitch_data(pattern):
        """Encode only the stitch point coordinates (no header, no framing).

        9mm:  each stitch → 5 ASCII decimal chars ``XXX`` + ``YY``.
        MAXI: each stitch → 7 ASCII decimal chars ``XXX`` + ``YY`` + ``sT``
              where s is '+' or '-' and T is the transport-delta magnitude (0 or 6).

        Returns:
            tuple[bytes, list[tuple[int, int]]]: A pair of
                ``(encoded_bytes, final_points)`` where ``final_points`` is the
                list of ``(x, y)`` coordinates exactly as the machine will
                interpret them — after the y-offset, any inserted intermediate
                stitches, and all transport adjustments applied by
                :meth:`_translate_maxi_points`.

        Raises:
            MachineCommError: If the stitch type is not supported.
        """
        if pattern.stitch_type == "9mm":
            elems = [(e[1], e[2]) for e in pattern.rounded_display_elements() if elem_has_coords(e)]
            translated_xy = MachineComm._translate_9mm_points(elems)
            encoded = ''.join(f"{x:03d}{y:02d}" for x, y in translated_xy).encode('ascii')
            return encoded, translated_xy
        elif pattern.stitch_type == "MAXI":
            raw_elems = [e for e in pattern.rounded_display_elements() if elem_has_coords(e)]
            if not raw_elems:
                return b'', []
            translated_xyt, translated_xy = MachineComm._translate_maxi_points(raw_elems)
            encoded = ''.join(
                f"{x:03d}{sy:02d}{'+' if d >= 0 else '-'}{abs(d):1d}"
                for x, sy, d in translated_xyt
            ).encode('ascii')
            return encoded, translated_xy
        else:
            raise MachineCommError(
                _tr("Unsupported stitch type for P-Memory encoding: {0}").format(repr(pattern.stitch_type))
            )

    # ── Pattern translations ──

    @staticmethod
    def _translate_9mm_points(elems):
        """Translate raw 9mm stitch elements into a list of (x, y) tuples.

        Applies x-offset so min(x) becomes 0.  No y-offset or transport adjustments
        are needed for 9mm patterns since the machine accepts absolute y values in
        the full range [0, 53].

        Args:
            elems: Iterable of elements with coords (e[0]=x, e[1]=y).

        Returns:
            list[tuple[int, int]]: Each entry is (x, y).
        """
        pts = [[e[0], e[1]] for e in elems]
        if not pts:
            return []
        x_offset = min(pt[0] for pt in pts)
        for pt in pts:
            pt[0] -= x_offset
            
        return [(pt[0], pt[1]) for pt in pts]

    @staticmethod
    def _translate_maxi_points(raw_elems):
        """Translate raw MAXI stitch elements into a list of (x, stored_y, delta) tuples.

        Applies x-offset so min(x) becomes 0.

        Applies a y-offset so the first stitch begins at stored_y = 27 (centre of
        the valid machine range [0, 54]).  Assigns a per-stitch transport_delta of
        0, +6, or -6 so that ``stored_y = effective_y - cumulative_transport`` stays
        within [0, 54].

        The first stitch's delta is always 0 (stored_y = 27 guaranteed by the
        y-offset) and is never modified by any later adjustment.

        When a stitch cannot be reached from the current transport position:
          1. A retroactive ±6 fix is attempted on earlier stitches at indices ≥ 1
             (stitch 0 is never touched).
          2. If that also fails, equally-spaced intermediate stitches are inserted
             between the preceding stitch and the unreachable one; each intermediate
             carries delta = ±6 and stored_y = 27.

        The last stitch's stored_y is steered towards 27 by adjusting earlier
        stitches (indices ≥ 1, never 0) in ±6 steps.  The last stitch itself may
        carry any integer delta in [-6, 6].

        Args:
            raw_elems: Iterable of elements with coords (e[1]=x, e[2]=y).

        Returns:
            list[tuple[int, int, int]]: Each entry is (x, stored_y, delta)
                where delta is 0 or ±6 for all stitches except possibly the last.
            list[tuple[int, int]]: Each entry is (x, y), matching the final stitch coordinates after applying all transport adjustments.
        """
        pts = [[e[1], e[2], 0] for e in raw_elems]
        if not pts:
            return [], []

        # Step 1: x-offset so min(x) becomes 0 and y-offset so the first stitch lands at stored_y = 27.
        xs = [pt[0] for pt in pts]
        x_offset = min(xs)
        y_offset = 27 - pts[0][1]
        for pt in pts:
            pt[0] -= x_offset
            pt[1] += y_offset

        # Step 2: forward pass – assign transport_deltas so stored_y stays in [0, 54].
        # Stitch 0 is never modified (delta stays 0, stored_y = 27).
        # When a stitch is unreachable even after retroactive fixes on stitches ≥ 1,
        # equally-spaced intermediate stitches (delta = ±6, stored_y = 27) are
        # inserted before it.
        transport_at = [0] * len(pts)

        i = 0
        while i < len(pts):
            ey = pts[i][1]
            prev_t = transport_at[i - 1] if i > 0 else 0

            chosen = None
            for d in [0, 6, -6]:
                if 0 <= ey - (prev_t + d) <= 54:
                    chosen = d
                    break

            if chosen is not None:
                pts[i][2] = chosen
                transport_at[i] = prev_t + chosen
                i += 1
                continue

            # Not directly reachable – try retroactive fix on stitches 1..i-1.
            gap = ey - prev_t
            direction = 6 if gap > 54 else -6
            fixed = False
            for j in range(i - 1, 0, -1):   # never touches stitch 0
                new_delta_j = pts[j][2] + direction
                if abs(new_delta_j) > 6:
                    continue
                if all(
                    0 <= pts[k][1] - (transport_at[k] + direction) <= 54
                    for k in range(j, i)
                ):
                    pts[j][2] = new_delta_j
                    for k in range(j, i):
                        transport_at[k] += direction
                    fixed = True
                    break

            if fixed:
                continue  # retry stitch i with updated transport

            # Retroactive fix also failed – insert intermediate stitches.
            # Compute how many ±6 steps are needed so that the original stitch
            # becomes reachable (gap lands in [-6, 60]).
            if gap > 60:
                step_dir = 6
                n_inter = max(1, (gap - 55) // 6)   # ceil((gap - 60) / 6)
            else:  # gap < -6
                step_dir = -6
                n_inter = max(1, (-1 - gap) // 6)   # ceil((-6 - gap) / 6)

            prev_x = pts[i - 1][0] if i > 0 else pts[i][0]
            prev_ey = pts[i - 1][1] if i > 0 else pts[i][1]
            curr_x = pts[i][0]
            curr_ey = pts[i][1]
            intermediates = []
            inter_transport = []
            running_t = prev_t
            for k in range(n_inter):
                frac = (k + 1) / (n_inter + 1)
                inter_x = round(prev_x + frac * (curr_x - prev_x))
                inter_ey = round(prev_ey + frac * (curr_ey - prev_ey))
                running_t += step_dir
                intermediates.append([inter_x, inter_ey, step_dir])
                inter_transport.append(running_t)

            pts[i:i] = intermediates
            transport_at[i:i] = inter_transport
            i += n_inter  # skip inserted intermediates; retry the original stitch

        # Step 2b: steer the last stitch's stored_y towards 27.
        # Adjust earlier stitches (indices 1..n-2, never 0) in ±6 steps until
        # the remaining gap for the last stitch is within [-6, 6], then assign
        # an exact delta in [-6, 6] to the last stitch.
        n = len(pts)
        if n >= 2:
            ey_last = pts[n - 1][1]
            needed_final = ey_last - 27  # desired cumulative transport after last stitch

            while True:
                prev_t = transport_at[n - 2]
                remaining = needed_final - prev_t
                if -6 <= remaining <= 6:
                    break
                direction = 6 if remaining > 6 else -6
                applied = False
                for j in range(n - 2, 0, -1):  # never touches stitch 0
                    new_delta_j = pts[j][2] + direction
                    if abs(new_delta_j) > 6:
                        continue
                    if all(
                        0 <= pts[k][1] - (transport_at[k] + direction) <= 54
                        for k in range(j, n - 1)
                    ):
                        pts[j][2] = new_delta_j
                        for k in range(j, n - 1):
                            transport_at[k] += direction
                        applied = True
                        break
                if not applied:
                    break

            prev_t = transport_at[n - 2]
            final_delta = max(-6, min(6, needed_final - prev_t))
            pts[n - 1][2] = final_delta
            transport_at[n - 1] = prev_t + final_delta

        # Step 3: resolve stored_y for each stitch.
        result_xyt = []
        result_xy = []
        transport = 0
        for x, ey, delta in pts:
            transport += delta
            stored_y = max(0, min(54, ey - transport))
            result_xyt.append((x, stored_y, delta))
            result_xy.append((x, stored_y + transport))
        return result_xyt, result_xy

    # ── Context manager support ──

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # ── Internal helpers ──

    @staticmethod
    def _require_model(machine_model):
        """Return the registered model for *machine_model*, or raise.

        Args:
            machine_model (str): Canonical model name from the configuration.

        Returns:
            machine_models.MachineModel: The matching registry entry.

        Raises:
            MachineCommError: If the name is not a registered model.
        """
        model = find_model(machine_model)
        if model is None:
            raise MachineCommError(
                _tr("Unknown machine model: {0}").format(repr(machine_model)))
        return model

    def _require_open(self):
        if not self.is_open:
            raise serial.SerialException(_tr("Serial port is not open."))
