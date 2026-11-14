"""A dependency-free QR Code (model 2) encoder.

Byte-mode encoding, versions 1-40, EC levels L/M/Q/H, following ISO/IEC 18004 so
LocalDrop can show the network URL as a scannable code with no internet access and
no third-party package. The error-correction tables are validated against the
standard's published character-capacity figures in tests/test_qr.py.
"""

EXP = [0] * 512
LOG = [0] * 256


def _build_gf_tables():
    x = 1
    for i in range(255):
        EXP[i] = x
        LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D  # x^8 + x^4 + x^3 + x^2 + 1
    for i in range(255, 512):
        EXP[i] = EXP[i - 255]


_build_gf_tables()

# Error-correction codewords per block, index 0 == version 1.
ECC_PER_BLOCK = {
    "L": [7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28,
          28, 28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    "M": [10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26,
          26, 26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28],
    "Q": [13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26,
          30, 28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
    "H": [17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26,
          28, 30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30],
}

# Number of error-correction blocks, index 0 == version 1.
BLOCK_COUNT = {
    "L": [1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8, 8, 9, 9, 10,
          12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25],
    "M": [1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16, 17, 17,
          18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49],
    "Q": [1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20, 23, 23,
          25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68],
    "H": [1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25, 25, 34,
          30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81],
}

FORMAT_LEVEL_BITS = {"L": 1, "M": 0, "Q": 3, "H": 2}
FORMAT_POLYNOMIAL = 0x537      # x^10 + x^8 + x^5 + x^4 + x^2 + x + 1
FORMAT_XOR_MASK = 0x5412
VERSION_POLYNOMIAL = 0x1F25    # x^12 + x^11 + x^10 + x^8 + x^5 + x^2 + 1
MAX_VERSION = 40
MODE_BYTE = 0b0100
PAD_BYTES = (0xEC, 0x11)


# --------------------------------------------------------------- arithmetic ---
def gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return EXP[LOG[a] + LOG[b]]


def poly_remainder(value, divisor):
    """Polynomial (bit) division over GF(2); used for BCH format/version info."""
    remainder = value
    shift = remainder.bit_length() - divisor.bit_length()
    while shift >= 0:
        remainder ^= divisor << shift
        shift = remainder.bit_length() - divisor.bit_length()
    return remainder


def rs_generator(degree):
    """Monic generator polynomial, highest power first (degree + 1 coefficients)."""
    poly = [1]
    for i in range(degree):
        nxt = poly + [0]
        for j in range(len(poly)):
            nxt[j + 1] ^= gf_mul(poly[j], EXP[i])
        poly = nxt
    return poly


def rs_remainder(data, degree):
    """Systematic Reed-Solomon check symbols for `data` (list of ints)."""
    gen = rs_generator(degree)
    state = [0] * degree
    for byte in data:
        factor = byte ^ state[0]
        state = state[1:] + [0]
        for i in range(degree):
            state[i] ^= gf_mul(gen[i + 1], factor)
    return state


def format_bits(level, mask):
    data = (FORMAT_LEVEL_BITS[level] << 3) | mask
    value = (data << 10) | poly_remainder(data << 10, FORMAT_POLYNOMIAL)
    return value ^ FORMAT_XOR_MASK


def version_bits(version):
    if version < 7:
        return 0
    return (version << 12) | poly_remainder(version << 12, VERSION_POLYNOMIAL)


# ------------------------------------------------------------------ matrix ----
class QR(object):
    """A finished symbol: `modules[row][col] is True` when dark."""

    def __init__(self, version, level, mask):
        self.version = version
        self.level = level
        self.mask = mask
        self.size = version * 4 + 17
        self.modules = [[False] * self.size for _ in range(self.size)]
        self.function = [[False] * self.size for _ in range(self.size)]

    # -- primitive setters ---------------------------------------------
    def _set(self, row, col, dark, is_function=True):
        if 0 <= row < self.size and 0 <= col < self.size:
            self.modules[row][col] = bool(dark)
            if is_function:
                self.function[row][col] = True

    def _finder(self, row, col):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                dist = max(abs(dx), abs(dy))
                self._set(row + dy, col + dx, dist != 2 and dist != 4)

    def _alignment(self, row, col):
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                self._set(row + dy, col + dx, max(abs(dx), abs(dy)) != 1)

    def _draw_function_patterns(self):
        size = self.size
        for i in range(size):
            self._set(6, i, i % 2 == 0)   # horizontal timing pattern
            self._set(i, 6, i % 2 == 0)   # vertical timing pattern
        self._finder(3, 3)
        self._finder(3, size - 4)
        self._finder(size - 4, 3)
        for pos in alignment_positions(self.version):
            for other in alignment_positions(self.version):
                if (pos == 6 and other == 6) or (pos == 6 and other == size - 7) \
                        or (pos == size - 7 and other == 6):
                    continue
                self._alignment(pos, other)
        # Reserve the format-information areas (drawn per mask later).
        self._draw_format(0)
        self._draw_version()

    def _draw_format(self, mask):
        """Both copies of the 15-bit format word; coordinates are (row, column)."""
        bits = format_bits(self.level, mask)
        size = self.size
        for i in range(6):
            self._set(i, 8, (bits >> i) & 1)              # upper-left, below timing
        self._set(7, 8, (bits >> 6) & 1)
        self._set(8, 8, (bits >> 7) & 1)
        self._set(8, 7, (bits >> 8) & 1)
        for i in range(9, 15):
            self._set(8, 14 - i, (bits >> i) & 1)         # upper-left, right of timing
        for i in range(8):
            self._set(8, size - 1 - i, (bits >> i) & 1)   # upper-right row
        for i in range(8, 15):
            self._set(size - 15 + i, 8, (bits >> i) & 1)  # lower-left column
        self._set(size - 8, 8, True)                      # fixed dark module

    def _draw_version(self):
        if self.version < 7:
            return
        bits = version_bits(self.version)
        size = self.size
        for i in range(18):
            dark = (bits >> i) & 1
            self._set(size - 11 + i % 3, i // 3, dark)     # lower-left block
            self._set(i // 3, size - 11 + i % 3, dark)     # upper-right block

    def _reserve_format_area(self):
        size = self.size
        for i in range(9):
            if i != 6:
                self.function[8][i] = True
                self.function[i][8] = True
        for i in range(8, 15):
            self.function[8][size - 15 + i] = True
            self.function[size - 15 + i][8] = True
        for i in range(0, 8):
            self.function[8][size - 1 - i] = True
            self.function[size - 1 - i][8] = True
        self.function[size - 8][8] = True

    def _draw_codewords(self, codewords):
        """Two-module column pairs, right to left, alternating up and down."""
        size = self.size
        bits = []
        for word in codewords:
            for shift in range(7, -1, -1):
                bits.append((word >> shift) & 1)
        total = len(bits)
        index = 0
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5
            upward = ((right + 1) & 2) == 0
            offsets = range(size) if not upward else range(size - 1, -1, -1)
            for vert in offsets:
                for step in (0, 1):
                    col = right - step
                    if self.function[vert][col]:
                        continue
                    if index >= total:
                        return
                    self.modules[vert][col] = bool(bits[index])
                    index += 1
            right -= 2

    def _apply_mask(self, mask):
        size = self.size
        formula = MASKS[mask]
        for row in range(size):
            for col in range(size):
                if not self.function[row][col] and formula(col, row):
                    self.modules[row][col] = not self.modules[row][col]

    # -- output ---------------------------------------------------------
    def is_dark(self, row, col):
        if 0 <= row < self.size and 0 <= col < self.size:
            return self.modules[row][col]
        return False

    def to_svg(self, scale=6, quiet=4, dark="#000000", light="#ffffff"):
        total = (self.size + quiet * 2) * scale
        parts = [
            '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d" shape-rendering="crispEdges">'
            % (total, total, total, total),
            '<rect width="%d" height="%d" fill="%s"/>' % (total, total, light),
        ]
        path = []
        for row in range(self.size):
            modules = self.modules[row]
            for col in range(self.size):
                if modules[col]:
                    x = (col + quiet) * scale
                    y = (row + quiet) * scale
                    path.append("M%d %dh%dv%dh-%dz" % (x, y, scale, scale, scale))
        parts.append('<path d="%s" fill="%s"/>' % ("".join(path), dark))
        parts.append("</svg>")
        return "".join(parts)

    def to_ascii(self, invert=False, compact=True):
        """Terminal preview; each QR module becomes one or two characters."""
        block = "\u2588\u2588" if not invert else "  "
        space = "  " if not invert else "\u2588\u2588"
        lines = []
        for row in range(self.size):
            out = []
            for col in range(self.size):
                dark = self.modules[row][col]
                if invert:
                    dark = not dark
                out.append(block if dark else space)
            lines.append("".join(out))
        if compact:
            return "\n".join(lines)
        return "\n".join(lines)


MASKS = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: (x * y) % 2 + (x * y) % 3 == 0,
    lambda x, y: ((x * y) % 2 + (x * y) % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + (x * y) % 3) % 2 == 0,
)


def alignment_positions(version):
    if version == 1:
        return []
    count = version // 7 + 2
    if version == 32:
        step = 26
    else:
        step = ((version * 8 + count * 3 + 5) // (count * 4 - 4)) * 2
    size = version * 4 + 17
    positions = [size - 7 - i * step for i in range(count - 1)] + [6]
    positions.sort()
    return positions


def raw_data_modules(version):
    result = (16 * version + 128) * version + 64
    if version >= 2:
        num_align = version // 7 + 2
        result -= (25 * num_align - 10) * num_align - 55
        if version >= 7:
            result -= 36
    return result


def total_codewords(version):
    return raw_data_modules(version) // 8


def data_codewords(version, level):
    blocks = BLOCK_COUNT[level][version - 1]
    ecc = ECC_PER_BLOCK[level][version - 1]
    return total_codewords(version) - blocks * ecc


def byte_capacity(version, level):
    """Whole bytes this version/level can carry in byte mode."""
    bits = data_codewords(version, level) * 8
    header = 4 + (8 if version < 10 else 16)
    return (bits - header) // 8


def choose_version(length, level):
    for version in range(1, MAX_VERSION + 1):
        if byte_capacity(version, level) >= length:
            return version
    raise ValueError("data too long for a QR symbol (%d bytes)" % length)


def _penalty(qr):
    size = qr.size
    modules = qr.modules
    score = 0

    # Rule 1: runs of five or more same-coloured modules.
    for line in modules + [list(row) for row in zip(*modules)]:
        run = 1
        for i in range(1, size):
            if line[i] == line[i - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)

    # Rule 2: 2x2 blocks of one colour.
    for row in range(size - 1):
        for col in range(size - 1):
            value = modules[row][col]
            if value == modules[row][col + 1] == modules[row + 1][col] == modules[row + 1][col + 1]:
                score += 3

    # Rule 3: finder-like 1:1:3:1:1 patterns with four light modules alongside.
    pattern = [True, False, True, True, True, False, True]
    for line in modules + [list(row) for row in zip(*modules)]:
        for start in range(size - 7):
            window = line[start:start + 7]
            if window == pattern:
                before = line[max(0, start - 4):start]
                after = line[start + 7:min(size, start + 11)]
                if len(before) == 4 and all(not v for v in before):
                    score += 40
                elif len(after) == 4 and all(not v for v in after):
                    score += 40

    # Rule 4: proportion of dark modules.
    dark = sum(1 for row in modules for value in row if value)
    percent = dark * 100 // (size * size)
    score += 10 * (abs(percent - 50) // 5)
    return score


def encode(text, level="M", mask=None):
    """Encode `text` (UTF-8 bytes) into a :class:`QR` symbol."""
    if isinstance(text, str):
        payload = text.encode("utf-8")
    else:
        payload = bytes(text or b"")
    level = (level or "M").upper()
    if level not in FORMAT_LEVEL_BITS:
        raise ValueError("unknown EC level %r" % level)

    version = choose_version(len(payload), level)
    num_blocks = BLOCK_COUNT[level][version - 1]
    ecc_len = ECC_PER_BLOCK[level][version - 1]
    total_data = data_codewords(version, level)

    # Bit stream: mode, length, payload, terminator, padding.
    bits = []
    count_width = 8 if version < 10 else 16
    for value, width in ((MODE_BYTE, 4), (len(payload), count_width)):
        for shift in range(width - 1, -1, -1):
            bits.append((value >> shift) & 1)
    for byte in payload:
        for shift in range(7, -1, -1):
            bits.append((byte >> shift) & 1)
    capacity_bits = total_data * 8
    for _ in range(min(4, capacity_bits - len(bits))):
        bits.append(0)
    while len(bits) % 8:
        bits.append(0)
    codewords = []
    for i in range(0, len(bits), 8):
        value = 0
        for bit in bits[i:i + 8]:
            value = (value << 1) | bit
        codewords.append(value)
    pad_index = 0
    while len(codewords) < total_data:
        codewords.append(PAD_BYTES[pad_index % 2])
        pad_index += 1

    # Split into blocks (longer blocks first), add EC codewords, interleave.
    short_len = total_data // num_blocks
    num_long = total_data % num_blocks
    blocks, ecc_blocks, cursor = [], [], 0
    for i in range(num_blocks):
        length = short_len + (1 if i < num_long else 0)
        block = codewords[cursor:cursor + length]
        cursor += length
        blocks.append(block)
        ecc_blocks.append(rs_remainder(block, ecc_len))

    stream = []
    for i in range(short_len + (1 if num_long else 0)):
        for j in range(num_blocks):
            if i == short_len and j >= num_long:
                continue
            stream.append(blocks[j][i])
    for i in range(ecc_len):
        for j in range(num_blocks):
            stream.append(ecc_blocks[j][i])

    return _place(stream, version, level, mask)


def _place(codewords, version, level, mask):
    qr = QR(version, level, 0)
    qr._draw_function_patterns()
    qr._reserve_format_area()
    qr._draw_codewords(codewords)

    candidates = range(8) if mask is None else (mask,)
    best = None
    for candidate in candidates:
        qr._apply_mask(candidate)
        qr._draw_format(candidate)
        score = _penalty(qr)
        qr._apply_mask(candidate)  # XOR is its own inverse
        if best is None or score < best[0]:
            best = (score, candidate)
    chosen = best[1]
    qr._apply_mask(chosen)
    qr._draw_format(chosen)
    qr.mask = chosen
    qr.penalty = best[0]
    return qr


def generate(text, level="M", scale=6, quiet=4):
    """One-call helper returning (QR, svg_string)."""
    qr = encode(text, level)
    return qr, qr.to_svg(scale=scale, quiet=quiet)
