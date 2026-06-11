"""Serial communication interface for PFAFF sewing machines."""

import re
import time
import os
from datetime import datetime
import serial
import serial.tools.list_ports
from PyQt5.QtCore import QCoreApplication
from model import elem_has_coords


def _tr(s):
    """Translate a string in the MachineComm context."""
    return QCoreApplication.translate("MachineComm", s)


class MachineCommError(Exception):
    """Raised when communication with the machine fails or gives an unexpected response."""


class _CommLogger:
    """Buffered direction-aware communication logger.

    Accumulates bytes sent in one direction and flushes a timestamped line
    whenever the direction changes or the logger is closed.
    """

    def __init__(self, log_dir):
        self._file = None
        self._log_dir = log_dir
        self._current_direction = None  # 'send' or 'receive'
        self._buffer = bytearray()

    def _ensure_file(self):
        if self._file is None:
            os.makedirs(self._log_dir, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"log_{timestamp}.txt"
            self._file = open(os.path.join(self._log_dir, filename), "w", encoding="utf-8")

    def _flush(self):
        if self._buffer and self._current_direction is not None:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            arrow = "->" if self._current_direction == "send" else "<-"
            hex_bytes = " ".join(f"{b:02X}" for b in self._buffer)
            self._file.write(f"[{ts}] {arrow} {hex_bytes}\n")
            self._file.flush()
            self._buffer.clear()

    def log_send(self, data):
        self._ensure_file()
        if self._current_direction == "receive":
            self._flush()
        self._current_direction = "send"
        self._buffer.extend(data)

    def log_receive(self, data):
        self._ensure_file()
        if self._current_direction == "send":
            self._flush()
        self._current_direction = "receive"
        self._buffer.extend(data)

    def log_info(self, message):
        """Write a high-level info line, flushing any pending data first."""
        self._ensure_file()
        self._flush()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        self._file.write(f"[{ts}] ## {message}\n")
        self._file.flush()

    def close(self):
        if self._file is not None:
            self._flush()
            self._file.close()
            self._file = None


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

    def __init__(self):
        self._serial = None
        self._logger = None
        self._log_enabled = False
        self._log_dir = None

    # Class-level reference for static methods that need to log errors.
    _active_logger = None

    def enable_logging(self, log_dir):
        """Enable communication logging.

        One log file is created per application run.  The file is created
        lazily on the first write/read after :meth:`open`.  Call before
        opening the serial port.

        Args:
            log_dir (str): Directory where log files will be written.
        """
        self._log_enabled = True
        self._log_dir = log_dir
        if self._logger is None:
            self._logger = _CommLogger(log_dir)

    def disable_logging(self):
        """Disable communication logging and close any open log file."""
        self._log_enabled = False
        if self._logger is not None:
            self._logger.close()
            self._logger = None
        self._log_dir = None

    def _log_info(self, message):
        """Write a high-level debug message to the log, if logging is active."""
        if self._logger is not None:
            self._logger.log_info(message)

    def _log_error(self, message):
        """Write an error message to the log, if logging is active."""
        self._log_info(f"ERROR: {message}")

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

        if self._log_enabled and self._log_dir:
            self._serial = _LoggedSerial(self._serial, self._logger)
            MachineComm._active_logger = self._logger

    def close(self):
        """Close the serial connection if open."""
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None
        MachineComm._active_logger = None

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

    # Maps substrings found in the ident response to canonical model names.
    # Checked in order; first match wins.
    _KNOWN_MACHINES = [
        ("7570",   "PFAFF Creative 7570"),
        ("7560",   "PFAFF Creative 7560"),
        ("7550 CD","PFAFF Creative 7550"),
        ("1475 CD","PFAFF Creative 1475 CD"),
    ]

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

        self._log_info(f"query_machine(retries={retries}, timeout={timeout})")

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
                    self._log_error(f"Unexpected identification response: {text!r}")
                    raise MachineCommError(
                        _tr("Unexpected identification response: {0}").format(repr(text))
                    )

                canonical = None
                for substring, name in self._KNOWN_MACHINES:
                    if substring in text:
                        canonical = name
                        break
                if canonical is None:
                    self._log_error(f"Unrecognised machine model in response: {text!r}")
                    raise MachineCommError(
                        _tr("Unrecognised machine model in response: {0}").format(repr(text))
                    )

                match = self._IDENT_PATTERN.search(text)
                return {
                    'raw':          text,
                    'machine_type': match.group(1).strip() if match else '',
                    'version':      match.group(2).strip() if match else '',
                    'model':        canonical,
                }

            # No response after all retries — signal the machine to abort
            self._serial.write(bytes([self.CTRL_EOT]))
            self._log_error("Machine not responding after all retries")
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

        self._log_info("query_pmemory_index()")

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

        self._log_info(f"delete_pmemory_slot(slot_index={slot_index})")

        saved_timeout = self._serial.timeout
        self._serial.timeout = timeout
        try:
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

        self._log_info(
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
        """

        self._log_info(f"send_pmemory_slot(slot_index={slot_index})")

        if "1475" in machine_model:
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
            stitch_type_byte = 0x00 if pattern.stitch_type == "9mm" else 0x01

            # ── Pre-compute stitch data and final machine-side points ──────
            stitch_data, final_points = self.encode_pmemory_stitch_data(pattern)
            expected_size = len(final_points) * 2 if pattern.stitch_type == "9mm" else len(final_points) * 3

            # ── Phase 1: write command ─────────────────────────────────────
            cmd_payload = (
                f"PN{slot_index:02X}{stitch_type_byte:02X}{expected_size:04X}"
            ).encode('ascii')
            cs = self.checksum(cmd_payload)

            self._log_info(f"send_pmemory_slot(slot_index={slot_index}) - write command")

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

            self._log_info(f"send_pmemory_slot(slot_index={slot_index}) - write header")

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

                self._log_info(
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
        Phase 2 - Stitch data (split into chunk_size-byte chunks):
            chunk + CTRL_ETB + 2-hex checksum
            last chunk has CTRL_ETX appended after the checksum.
            CTRL_EOT is sent after the last chunk to signal end of transmission.

        The machine must reply with CTRL_ENQ after write command, 
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

            self._log_info(f"send_pmemory_slot(slot_index={slot_index}) - write command")

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

            # ── Phase 2: stitch data chunks ────────────────────────────────
            total = len(stitch_data)
            for offset in range(0, total, chunk_size):
                chunk = stitch_data[offset:offset + chunk_size]
                cs = self.checksum(chunk)
                is_last_chunk = (offset + chunk_size) >= total

                self._log_info(
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

    # ── Memory Card commands ──

    def query_card_index(self, timeout=1.0):
        """Query the machine memory card index.

        Sends "KI" + CTRL_ETX and reads the response.

        If the machine replies with CTRL_NAK + CTRL_BS there is no card
        inserted; CTRL_EOT is sent and MachineCommError is raised.

        Response format (raw bytes before CTRL_ETB)::

            06 00 00 <CardNo[2]> <PayloadSize>
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

        self._log_info("query_card_index()")

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
                # Second byte is CTRL_BS; read and discard it
                self._serial.read(1)
                raise MachineCommError(_tr("No memory card inserted in the machine."))

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

            CTRL_ACK + 4 unknown bytes + SIZE(2) + NAME_SIZE(1) + NAME
            + PayloadSize(1) + Payload + PayloadSize(1) + CTRL_ETB
            + checksum

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
            }

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: On timeout, unexpected response, or checksum error
                after all retries.
        """
        self._require_open()

        card_id = ''.join(f'{b:02X}' for b in card_no_bytes)
        self._log_info(
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
            self._serial.write(cmd)

            name = ''
            size = 0
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

                unknown4 = self._serial.read(4)
                if len(unknown4) < 4:
                    raise MachineCommError(_tr("Timeout reading unknown bytes in first chunk."))

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

                cs_data = (unknown4 + size_bytes + name_size_raw
                           + name_raw + ps_raw + chunk_payload + ps_repeat_raw)
                received_cs = int(cs_raw.decode('ascii'), 16)

                expected_cs = self.checksum(cs_data)

                if received_cs == expected_cs:
                    name = name_raw.rstrip(b'\x00').decode('latin-1', errors='replace')
                    size = int.from_bytes(size_bytes, 'big')
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
        self._log_info(
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
        self._log_info(
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

        Data chunks::

            CTRL_ENQ + SIZE + <payload> + SIZE + CTRL_ETB + <2 hex checksum>

        Checksum covers: CTRL_ENQ + SIZE_BYTE + payload + SIZE_BYTE.
        Machine acknowledges each chunk with CTRL_ACK; on CTRL_NAK the chunk
        is retransmitted (max 3 retries).

        Args:
            card_no_bytes (bytes): Raw 2-byte card number from query_card().
            pattern: StitchPattern instance used to derive KN header parameters.
            filename (str): Filename (max 8 characters).
            timeout (float): Per-read timeout in seconds.  Default: 1.0.
            max_retries (int): Maximum number of retries for each chunk.  Default: 3.
            progress_callback: Optional ``(done_bytes, total_bytes)`` callable.

        Returns:
            int: The slot number assigned by the machine.

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: On timeout, CTRL_NAK, checksum error, or
                unexpected response.
        """
        self._require_open()

        card_id = ''.join(f'{b:02X}' for b in card_no_bytes)
        self._log_info(f"send_card_slot(card_no={card_id}, filename={filename!r})")

        stitch_type = pattern.stitch_type

        # Null-terminated, max 9 bytes (8 chars + '\0')
        filename_bytes = filename[:8].encode('latin-1', errors='replace') + b'\x00'

        # ── Build preview image and encode pattern data (before connecting) ──
        preview_bytes = self.encode_card_preview(pattern)

        # Encode pattern data according to stitch type
        if stitch_type == '9mm':
            stitch_data, final_points = MachineComm.encode_card_pattern_9mm(pattern)
        elif stitch_type == 'MAXI':
            stitch_data, final_points = MachineComm.encode_card_pattern_maxi(pattern)
        else:
            raise MachineCommError(_tr("Unsupported stitch type for card writing: {0}").format(repr(stitch_type)))
        
        header = self.encode_card_header(pattern, card_no_bytes, preview_bytes, stitch_data, filename_bytes, points=final_points)

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
                    _tr("No response to card write (KN) command.")
                )
            if resp[0] == self.CTRL_NAK:
                raise MachineCommError(
                    _tr("Machine rejected the card write command. The card may be full or write-protected.")
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

    @staticmethod
    def encode_card_preview(pattern):
        """Generate a column-major 1-bit-per-pixel preview image for memory card.

        Draws line segments between consecutive stitch points, scaled and
        centred within the type-specific preview dimensions:

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
        * Embroidery images are rotated 180° before encoding.

        Args:
            pattern: StitchPattern instance.

        Returns:
            bytes: Encoded preview bitmap.
                   Size: ``img_w * (img_h // 8)`` bytes.
        """
        stitch_type = pattern.stitch_type
        if stitch_type == '9mm':
            img_h, img_w = 24, 53
        elif stitch_type == 'MAXI':
            img_h, img_w = 48, 53
        else:   # Embroidery
            img_h, img_w = 48, 48

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

        # Rotate 180° for Embroidery
        if stitch_type == 'Embroidery':
            rotated = bytearray(img_w * img_h)
            for y in range(img_h):
                for x in range(img_w):
                    rotated[(img_h - 1 - y) * img_w + (img_w - 1 - x)] = (
                        bitmap[y * img_w + x]
                    )
            bitmap = rotated
        
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

        if points is None:
            points = [(e[1], e[2]) for e in pattern.rounded_display_elements() if elem_has_coords(e)]
        if not points:
            raise MachineCommError(_tr("Cannot encode header for an empty pattern."))
        
        stitch_type = pattern.stitch_type

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
                interval ``(-90, 90)``, i.e. ``|dx| >= 90``.  The error message
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
            if not (-90 < dx < 90):
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
                interval ``(-90, 90)``.  The error message names the offending
                stitch and advises inserting intermediate stitches.
        """
        raw_elems = [e for e in pattern.rounded_display_elements() if elem_has_coords(e)]
        if not raw_elems:
            return bytes([0x80, 0x8A]), []  # Empty pattern with leading sentinel

        translated_xyt, translated_xy = MachineComm._translate_maxi_points(raw_elems)

        encoded = bytearray([0x80])
        x_prev = translated_xyt[0][0]  # x of the first stitch
        for i, (x, stored_y, delta) in enumerate(translated_xyt):
            dx = x_prev - x
            if not (-90 < dx < 90):
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

        if "1475" in machine_model:
            bytes_per_slot = 14
        else:
            bytes_per_slot = 10

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

        return {
            'num_slots': num_slots,
            'free_memory': free_memory-1, # Original SW shows 1 byte less than machine reports, reason unknown
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
                f"{(span_y // 2)  & 0xFF:02X}"   # byte  2   y_max_norm_div_2 # ToDo: this is wrong! Find correct value!
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

    def _require_open(self):
        if not self.is_open:
            raise serial.SerialException(_tr("Serial port is not open."))
