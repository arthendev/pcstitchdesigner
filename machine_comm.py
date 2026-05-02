"""Serial communication interface for PFAFF sewing machines."""

import re
import time
import serial
import serial.tools.list_ports


class MachineCommError(Exception):
    """Raised when communication with the machine fails or gives an unexpected response."""


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
    CTRL_NAK = 0x15 # Negative Acknowledge
    CTRL_ETB = 0x17 # End of Transmission Block

    def __init__(self):
        self._serial = None

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

    # Maps substrings found in the ident response to canonical model names.
    # Checked in order; first match wins.
    _KNOWN_MACHINES = [
        ("7570",   "PFAFF Creative 7570"),
        ("7560",   "PFAFF Creative 7560"),
        ("7550 CD","PFAFF Creative 7550"),
        ("1475 CD","PFAFF Creative 1475 CD"),
    ]

    def query_machine(self, retries=15, retry_delay=0.05, timeout=2.0):
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
            timeout (float): Read timeout in seconds per attempt. Default: 2.0.

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
                    raise MachineCommError(
                        f"Unexpected identification response: {text!r}"
                    )

                canonical = None
                for substring, name in self._KNOWN_MACHINES:
                    if substring in text:
                        canonical = name
                        break
                if canonical is None:
                    raise MachineCommError(
                        f"Unrecognised machine model in response: {text!r}"
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
            raise MachineCommError(
                f"No response from machine after {retries} attempt(s)."
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

    def read_pfaff_chunk(self, timeout=1.0):
        """Read a PFAFF protocol chunk from the serial port.

        Reads bytes in a loop until CTRL_END_OF_TRANS_BLOCK (0x17) followed by
        2 ASCII hex checksum bytes is detected, or the timeout expires.
        Verifies the checksum and returns the payload.

        Args:
            timeout (float): Maximum seconds to wait for a complete chunk. Default: 1.0.

        Returns:
            bytes: Chunk payload (stripped of terminator and checksum).

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: If the timeout expires before a complete chunk is
                received, or if the checksum is invalid.
        """
        self._require_open()

        buf = bytearray()
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            if self._serial.in_waiting:
                buf.extend(self._serial.read(self._serial.in_waiting))

                # End condition: CTRL_ETB at [-3] + 2 checksum bytes
                if len(buf) >= 3 and buf[-3] == self.CTRL_ETB:
                    payload = bytes(buf[:-3])
                    received_checksum_bytes = bytes(buf[-2:])

                    # Decode two ASCII hex characters into an integer (e.g. b'A3' -> 0xA3)
                    try:
                        received_checksum = int(received_checksum_bytes.decode('ascii'), 16)
                    except (ValueError, UnicodeDecodeError) as exc:
                        raise MachineCommError(
                            f"Invalid checksum encoding: {received_checksum_bytes!r}"
                        ) from exc

                    expected_checksum = self.checksum(payload)
                    if received_checksum != expected_checksum:
                        raise MachineCommError(
                            f"Checksum mismatch: expected {expected_checksum:02X}, "
                            f"got {received_checksum:02X}"
                        )

                    return payload
            else:
                time.sleep(0.005)  # Short sleep to avoid busy-waiting

        raise MachineCommError(
            f"Timeout waiting for chunk after {timeout:.1f}s "
            f"(received {len(buf)} bytes so far)."
        )

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

    def query_pmemory(self, timeout=5.0):
        """Query the machine P-Memory directory.

        Sends the "PI" command terminated with CTRL_ETX. Reads the response
        until CTRL_ETB is received, then reads the 2 trailing checksum bytes.

        Args:
            timeout (float): Read timeout in seconds. Default: 5.0.

        Returns:
            bytes: Raw response bytes (payload bytes up to and including CTRL_ETB,
                   followed by the 2 ASCII-hex checksum bytes).

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: If the machine does not respond in time.
        """
        self._require_open()

        saved_timeout = self._serial.timeout
        self._serial.timeout = timeout
        try:
            self.flush()
            self._serial.write(b"PI" + bytes([self.CTRL_ETX]))

            data = self._serial.read_until(expected=bytes([self.CTRL_ETB]))
            if not data or data[-1] != self.CTRL_ETB:
                raise MachineCommError(
                    "Timeout waiting for P-Memory response (CTRL_ETB not received)."
                )

            checksum_bytes = self._serial.read(2)
            if len(checksum_bytes) < 2:
                raise MachineCommError(
                    "Timeout waiting for P-Memory checksum bytes."
                )

            self._serial.write(bytes([self.CTRL_ACK]))
            return data + checksum_bytes
        finally:
            self._serial.timeout = saved_timeout

    def delete_pmemory_slot(self, slot_index, timeout=5.0):
        """Send a delete command for the given P-Memory slot.

        Sends "PL<xx>" + CTRL_ETX where <xx> is the zero-based slot index
        encoded as two ASCII-hex characters, then waits for CTRL_ACK or CTRL_NAK.

        Args:
            slot_index (int): Zero-based index of the slot to delete.
            timeout (float): Read timeout in seconds. Default: 5.0.

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: With message "Machine refused deletion" on CTRL_NAK,
                or "Error during communication" on any other unexpected response.
        """
        self._require_open()

        saved_timeout = self._serial.timeout
        self._serial.timeout = timeout
        try:
            cmd = f"PL{slot_index:02X}".encode('ascii') + bytes([self.CTRL_ETX])
            self._serial.write(cmd)

            response = self._serial.read(1)
            if not response:
                raise MachineCommError("Error during communication")
            if response[0] == self.CTRL_NAK:
                raise MachineCommError("Machine refused deletion")
            if response[0] != self.CTRL_ACK:
                raise MachineCommError("Error during communication")
        finally:
            self._serial.timeout = saved_timeout

    def load_pmemory_slot(self, slot_index, slot_type, timeout=3.0,
                          total_size=0, progress_callback=None):
        """Load a pattern from a P-Memory slot.

        Sends 'RM06<XX><T>' + CTRL_ETX, where XX is the zero-based slot index
        as two ASCII-hex characters and T is '0' for 9mm or '1' for MAXI.

        The machine responds with a stream of hex-ASCII chunks, each terminated
        by CTRL_ETB + 2-char hex checksum.  The final chunk has CTRL_ETX
        appended after the checksum.  Each chunk is acknowledged with CTRL_ACK.

        Args:
            slot_index (int): Zero-based index of the slot to read.
            slot_type (str): '9mm' or 'MAXI'.
            timeout (float): Per-byte read timeout in seconds. Default: 3.0.

        Returns:
            bytes: Concatenated hex-ASCII payload from all chunks.

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: On NAK, checksum error, or communication timeout.
        """
        self._require_open()

        if slot_type == "9mm":
            type_char = "0"
        elif slot_type == "MAXI":
            type_char = "1"
        else:
            raise MachineCommError(f"Unknown slot type: {slot_type!r}")

        saved_timeout = self._serial.timeout
        self._serial.timeout = timeout
        try:
            cmd = f"RM06{slot_index:02X}{type_char}".encode('ascii') + bytes([self.CTRL_ETX])
            self._serial.write(cmd)

            # First byte: NAK means invalid request, otherwise start of first chunk
            first = self._serial.read(1)
            if not first:
                raise MachineCommError("No response from machine.")
            if first[0] == self.CTRL_NAK:
                raise MachineCommError("Machine refused the P-Memory read request.")

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
                        raise MachineCommError("Timeout waiting for chunk data.")
                    if b[0] == self.CTRL_ETB:
                        break
                    chunk_payload.extend(b)

                # Read 2 ASCII-hex checksum characters
                cs_bytes = self._serial.read(2)
                if len(cs_bytes) < 2:
                    raise MachineCommError("Timeout waiting for chunk checksum.")

                # Verify checksum
                try:
                    received_cs = int(cs_bytes.decode('ascii'), 16)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise MachineCommError(
                        f"Invalid checksum encoding: {cs_bytes!r}"
                    ) from exc

                payload = bytes(chunk_payload)
                expected_cs = self.checksum(payload)
                if received_cs != expected_cs:
                    self._serial.write(bytes([self.CTRL_NAK]))
                    raise MachineCommError(
                        f"Chunk checksum mismatch: expected {expected_cs:02X}, "
                        f"got {received_cs:02X}"
                    )

                receive_buffer.extend(payload)
                if progress_callback is not None:
                    progress_callback(len(receive_buffer), total_size)
                self._serial.write(bytes([self.CTRL_ACK]))

                # After ACK, read the first byte of the next chunk.
                # CTRL_ETX means the machine has no more data.
                next_b = self._serial.read(1)
                if not next_b:
                    raise MachineCommError("Timeout waiting for next chunk or end-of-data.")
                if next_b[0] == self.CTRL_ETX:
                    break
                carried = next_b  # first byte of the next chunk

            return bytes(receive_buffer)
        finally:
            self._serial.timeout = saved_timeout

    def send_pattern(self, data, chunk_size=250, timeout=5.0, progress_callback=None):
        """Send pattern data to the machine in chunked PFAFF protocol frames.

        Each chunk is wrapped as: <payload> + CTRL_ETB + <2 ASCII-hex checksum bytes>.
        After each frame the method waits for CTRL_ACK from the machine.

        Args:
            data (bytes | bytearray): Serialised pattern data to send.
            chunk_size (int): Payload size per chunk in bytes. Default: 250.
            timeout (float): Per-chunk read timeout in seconds. Default: 5.0.

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: If the machine replies with CTRL_NAK, sends an
                unexpected byte, or does not respond within the timeout.
        """
        self._require_open()

        saved_timeout = self._serial.timeout
        self._serial.timeout = timeout
        try:
            for offset in range(0, len(data), chunk_size):
                chunk = data[offset:offset + chunk_size]
                cs = self.checksum(chunk)
                frame = chunk + bytes([self.CTRL_ETB]) + f"{cs:02X}".encode('ascii')
                self._serial.write(frame)

                response = self._serial.read(1)
                if not response:
                    raise MachineCommError(
                        "Timeout waiting for acknowledgement after chunk "
                        f"{offset // chunk_size + 1}."
                    )
                if response[0] == self.CTRL_NAK:
                    raise MachineCommError(
                        "Error occurred while sending the pattern"
                    )
                if response[0] != self.CTRL_ACK:
                    raise MachineCommError(
                        f"Unexpected response 0x{response[0]:02X} during transfer."
                    )
                if progress_callback is not None:
                    progress_callback(min(offset + chunk_size, len(data)), len(data))
        finally:
            self._serial.timeout = saved_timeout

    def send_pmemory_slot(self, slot_index, pattern, chunk_size=250, timeout=5.0,
                          progress_callback=None):
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
            timeout (float): Per-response read timeout in seconds. Default: 5.0.
            progress_callback: Optional ``(done_bytes, total_bytes)`` callable
                called after each stitch-data chunk is acknowledged.

        Raises:
            serial.SerialException: If the port is not open.
            MachineCommError: On timeout, CTRL_NAK, or unexpected response.
        """
        self._require_open()

        stitch_type_byte = 0x00 if pattern.stitch_type == "9mm" else 0x01
        n = len(pattern.points)
        expected_size = n * 2 if pattern.stitch_type == "9mm" else n * 3

        saved_timeout = self._serial.timeout
        self._serial.timeout = timeout
        try:
            # ── Phase 1: write command ─────────────────────────────────────
            cmd_payload = (
                f"PN{slot_index:02X}{stitch_type_byte:02X}{expected_size:04X}"
            ).encode('ascii')
            cs = self.checksum(cmd_payload)
            self._serial.write(
                cmd_payload
                + bytes([self.CTRL_ETB])
                + f"{cs:02X}".encode('ascii')
                + bytes([self.CTRL_ETX])
            )
            resp = self._serial.read(1)
            if not resp:
                raise MachineCommError(
                    "Timeout waiting for acknowledgement after write command."
                )
            if resp[0] == self.CTRL_NAK:
                raise MachineCommError("Machine rejected the write command.")
            if resp[0] != self.CTRL_ENQ:
                raise MachineCommError(
                    f"Unexpected response 0x{resp[0]:02X} after write command."
                )

            # ── Phase 2: header ────────────────────────────────────────────
            header = self.encode_machine_header(pattern)
            cs = self.checksum(header)
            self._serial.write(
                header
                + bytes([self.CTRL_ETB])
                + f"{cs:02X}".encode('ascii')
                + bytes([self.CTRL_ETX])
            )
            resp = self._serial.read(1)
            if not resp:
                raise MachineCommError(
                    "Timeout waiting for acknowledgement after header."
                )
            if resp[0] == self.CTRL_NAK:
                raise MachineCommError("Machine rejected the header.")
            if resp[0] != self.CTRL_ENQ:
                raise MachineCommError(
                    f"Unexpected response 0x{resp[0]:02X} after header."
                )

            # ── Phase 3: stitch data chunks ────────────────────────────────
            data = self.encode_machine_stitch_data(pattern)
            total = len(data)
            for offset in range(0, total, chunk_size):
                chunk = data[offset:offset + chunk_size]
                cs = self.checksum(chunk)
                is_last_chunk = (offset + chunk_size) >= total
                self._serial.write(
                    chunk
                    + bytes([self.CTRL_ETB])
                    + f"{cs:02X}".encode('ascii')
                    + (bytes([self.CTRL_ETX]) if is_last_chunk else b'')
                )
                resp = self._serial.read(1)
                if not resp:
                    raise MachineCommError(
                        f"Timeout waiting for acknowledgement after chunk "
                        f"{offset // chunk_size + 1}."
                    )
                if resp[0] == self.CTRL_NAK:
                    raise MachineCommError("Machine rejected a stitch data chunk.")
                if resp[0] != self.CTRL_ACK:
                    raise MachineCommError(
                        f"Unexpected response 0x{resp[0]:02X} during stitch data transfer."
                    )
                if progress_callback is not None:
                    progress_callback(min(offset + chunk_size, total), total)
        finally:
            self._serial.timeout = saved_timeout

    # ── P-Memory decoding ──

    @staticmethod
    def decode_pmemory(raw_bytes, machine_model):
        """Decode raw P-Memory query response bytes into a structured dict.

        The expected layout of raw_bytes (as returned by query_pmemory) is:
            <payload ASCII chars> + CTRL_ETB + <2 ASCII-hex checksum chars>

        Payload structure (all values ASCII-hex encoded):
            [0:2]                     - number of slots (1 byte, hex)
            repeated num_slots times:
              [+0:+2]                 - stitch type (0x00 = 9mm, 0x01 = MAXI)
              [+2:+6]                 - number of stitches (2 bytes, hex)
              [+6:+10]                - unknown (4 chars, ignored)
            last 4 chars              - total free memory (2 bytes, unsigned hex)

        Args:
            raw_bytes (bytes): Raw bytes returned by query_pmemory().
            machine_model (str): Machine model string from configuration (reserved
                for future model-specific variations).

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
            raise MachineCommError("P-Memory response too short.")

        checksum_ascii = raw_bytes[-2:]
        etb_byte = raw_bytes[-3]
        payload = raw_bytes[:-3]

        if etb_byte != MachineComm.CTRL_ETB:
            raise MachineCommError(
                f"Expected CTRL_ETB at position -3, got 0x{etb_byte:02X}."
            )

        # Verify checksum
        try:
            received_checksum = int(checksum_ascii.decode('ascii'), 16)
        except (ValueError, UnicodeDecodeError) as exc:
            raise MachineCommError(
                f"Invalid checksum encoding: {checksum_ascii!r}"
            ) from exc

        expected_checksum = MachineComm.checksum(payload)
        if received_checksum != expected_checksum:
            raise MachineCommError(
                f"P-Memory checksum mismatch: expected {expected_checksum:02X}, "
                f"got {received_checksum:02X}"
            )

        # Decode payload as ASCII
        try:
            text = payload.decode('ascii')
        except UnicodeDecodeError as exc:
            raise MachineCommError("P-Memory payload is not valid ASCII.") from exc

        if len(text) < 2:
            raise MachineCommError("P-Memory payload too short to read slot count.")

        try:
            num_slots = int(text[0:2], 16)
        except ValueError as exc:
            raise MachineCommError(
                f"Invalid slot count encoding: {text[0:2]!r}"
            ) from exc

        expected_len = 2 + num_slots * 10 + 4
        if len(text) != expected_len:
            raise MachineCommError(
                f"P-Memory payload length mismatch: expected {expected_len} chars, "
                f"got {len(text)}."
            )

        slots = []
        offset = 2
        for i in range(num_slots):
            slot_text = text[offset:offset + 10]
            try:
                type_byte = int(slot_text[0:2], 16)
                size = int(slot_text[2:6], 16)
                # slot_text[6:10] - purpose unknown, ignored
            except ValueError as exc:
                raise MachineCommError(
                    f"Invalid data for slot {i + 1}: {slot_text!r}"
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
            offset += 10

        try:
            free_memory = int(text[offset:offset + 4], 16)
        except ValueError as exc:
            raise MachineCommError(
                f"Invalid free memory encoding: {text[offset:offset + 4]!r}"
            ) from exc

        return {
            'num_slots': num_slots,
            'free_memory': free_memory,
            'slots': slots,
        }

    @staticmethod
    def decode_machine_pattern(data, slot_type):
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
            raise MachineCommError("Pattern data is not valid ASCII.") from exc

        points = []

        if slot_type == "9mm":
            step = 5
            if len(text) % step != 0:
                raise MachineCommError(
                    f"9mm pattern data length {len(text)} is not a multiple of {step}."
                )
            for i in range(0, len(text), step):
                group = text[i:i + step]
                try:
                    x = int(group[0:3], 10)
                    y = int(group[3:5], 10)
                except ValueError as exc:
                    raise MachineCommError(
                        f"Invalid 9mm stitch data at offset {i}: {group!r}"
                    ) from exc
                points.append((x, y))

        elif slot_type == "MAXI":
            step = 7
            if len(text) % step != 0:
                raise MachineCommError(
                    f"MAXI pattern data length {len(text)} is not a multiple of {step}."
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
                        f"Invalid MAXI stitch data at offset {i}: {group!r}"
                    ) from exc
                if sign == '+':
                    maxi_transport += transport_delta
                elif sign == '-':
                    maxi_transport -= transport_delta
                else:
                    raise MachineCommError(
                        f"Invalid sign character {sign!r} at offset {i}."
                    )
                points.append((x, y + maxi_transport))

        else:
            raise MachineCommError(
                f"Unknown slot type for pattern decoding: {slot_type!r}"
            )

        return points

    @staticmethod
    def encode_machine_header(pattern):
        """Encode the fixed header for the given pattern.

        Returns ASCII-encoded bytes (no framing, no checksum).
        32 chars (16 bytes) — bytes 0-15.
        Each byte is represented as two uppercase hex digits.

        Raises:
            MachineCommError: If the stitch type is not supported or pattern is empty.
        """
        points = pattern.points
        if not points:
            raise MachineCommError("Cannot encode header for an empty pattern.")
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
        y_min_to_bound = 0x36 - min(ys)

        if pattern.stitch_type == "9mm":
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
                f"Unsupported stitch type for machine encoding: {pattern.stitch_type!r}"
            )

    @staticmethod
    def encode_machine_stitch_data(pattern):
        """Encode only the stitch point coordinates (no header, no framing).

        9mm:  each stitch → 5 ASCII decimal chars ``XXX`` + ``YY``.
        MAXI: each stitch → 7 ASCII decimal chars ``XXX`` + ``YY`` + ``+0``.

        Returns:
            bytes: ASCII-encoded stitch data.

        Raises:
            MachineCommError: If the stitch type is not supported.
        """
        if pattern.stitch_type == "9mm":
            return ''.join(
                f"{x:03d}{y:02d}" for x, y in pattern.points
            ).encode('ascii')
        elif pattern.stitch_type == "MAXI":
            return ''.join(
                f"{x:03d}{y:02d}+0" for x, y in pattern.points
            ).encode('ascii')
        else:
            raise MachineCommError(
                f"Unsupported stitch type for machine encoding: {pattern.stitch_type!r}"
            )

    # ── Context manager support ──

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    # ── Internal helpers ──

    def _require_open(self):
        if not self.is_open:
            raise serial.SerialException("Serial port is not open.")
