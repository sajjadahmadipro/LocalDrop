"""Independent verification of localdrop.qr.

The decoder here is written from the symbol geometry (finders, timing, alignment,
reserved areas, zigzag order) rather than by reusing the encoder's helpers, so a
round-trip through it catches placement, masking and format-bit mistakes.
"""

import unittest

from localdrop import qr

# Official byte-mode character capacities, ISO/IEC 18004 Table 7 (versions 1-10).
BYTE_CAPACITY = {
    1: (17, 14, 11, 7), 2: (32, 26, 20, 14), 3: (53, 42, 32, 24),
    4: (78, 62, 46, 34), 5: (106, 84, 60, 44), 6: (134, 106, 74, 58),
    7: (154, 122, 86, 64), 8: (192, 152, 108, 84), 9: (230, 180, 130, 98),
    10: (271, 213, 151, 119),
}

# Published format-information bit strings for mask 0.
FORMAT_MASK0 = {
    "L": 0b111011111000100,
    "M": 0b101010000010010,
    "Q": 0b011010101011111,
    "H": 0b001011010001001,
}


# --------------------------------------------------------------- decoding ----
def function_map(version):
    """Every module that is NOT available for data, built from geometry."""
    size = version * 4 + 17
    fn = [[False] * size for _ in range(size)]

    def mark(row, col, value=True):
        if 0 <= row < size and 0 <= col < size:
            fn[row][col] = value

    # Finder patterns plus their separators: three 8x8 corners.
    for (row, col) in ((0, 0), (0, size - 8), (size - 8, 0)):
        for r in range(8):
            for c in range(8):
                mark(row + r, col + c)
    # Timing patterns.
    for i in range(size):
        mark(6, i)
        mark(i, 6)
    # Alignment patterns (5x5), skipping the three that overlap finders.
    positions = qr.alignment_positions(version)
    for row in positions:
        for col in positions:
            if (row == 6 and col == 6) or (row == 6 and col == size - 7) \
                    or (row == size - 7 and col == 6):
                continue
            for r in range(row - 2, row + 3):
                for c in range(col - 2, col + 3):
                    mark(r, c)
    # Format information areas.
    for i in range(9):
        mark(8, i)
        mark(i, 8)
    for i in range(8):
        mark(8, size - 1 - i)
        mark(size - 1 - i, 8)
    # Version information areas.
    if version >= 7:
        for i in range(18):
            mark(i // 3, size - 11 + i % 3)
            mark(size - 11 + i % 3, i // 3)
    return fn


def read_format(modules):
    size = len(modules)
    bits = 0
    for i in range(6):
        bits |= (1 if modules[i][8] else 0) << i
    bits |= (1 if modules[7][8] else 0) << 6
    bits |= (1 if modules[8][8] else 0) << 7
    bits |= (1 if modules[8][7] else 0) << 8
    for i in range(9, 15):
        bits |= (1 if modules[8][14 - i] else 0) << i
    second = 0
    for i in range(8):
        second |= (1 if modules[8][size - 1 - i] else 0) << i
    for i in range(8, 15):
        second |= (1 if modules[size - 15 + i][8] else 0) << i
    return bits, second


def extract_bits(modules, fn):
    """Zigzag two-column sweep, skipping function modules."""
    size = len(modules)
    bits = []
    right = size - 1
    while right >= 1:
        if right == 6:
            right = 5
        upward = ((right + 1) & 2) == 0
        for vert in (range(size - 1, -1, -1) if upward else range(size)):
            for col in (right, right - 1):
                if not fn[vert][col]:
                    bits.append(1 if modules[vert][col] else 0)
        right -= 2
    return bits


def syndrome_zero(codewords, ecc_len):
    """Evaluate the block polynomial at alpha^0 .. alpha^(ecc_len-1)."""
    for i in range(ecc_len):
        root = qr.EXP[i]
        value = 0
        for byte in codewords:
            value = qr.gf_mul(value, root) ^ byte
        if value != 0:
            return False
    return True


def decode(qr_obj):
    version = qr_obj.version
    fn = function_map(version)
    first, second = read_format(qr_obj.modules)
    assert first == second, "the two format copies disagree"
    assert qr_obj.modules[qr_obj.size - 8][8] is True, "dark module missing"
    raw = first ^ qr.FORMAT_XOR_MASK
    assert qr.poly_remainder(raw, qr.FORMAT_POLYNOMIAL) == 0, "format BCH invalid"
    data = raw >> 10
    level = {v: k for k, v in qr.FORMAT_LEVEL_BITS.items()}[data >> 3]
    mask = data & 7
    assert level == qr_obj.level

    modules = [row[:] for row in qr_obj.modules]
    formula = qr.MASKS[mask]
    for row in range(qr_obj.size):
        for col in range(qr_obj.size):
            if not fn[row][col] and formula(col, row):
                modules[row][col] = not modules[row][col]

    bits = extract_bits(modules, fn)
    total = qr.total_codewords(version)
    assert len(bits) >= total * 8, "not enough data modules"
    words = []
    for i in range(total):
        value = 0
        for bit in bits[i * 8:(i * 8) + 8]:
            value = (value << 1) | bit
        words.append(value)

    num_blocks = qr.BLOCK_COUNT[level][version - 1]
    ecc_len = qr.ECC_PER_BLOCK[level][version - 1]
    data_total = qr.data_codewords(version, level)
    short_len = data_total // num_blocks
    num_long = data_total % num_blocks
    lengths = [short_len + (1 if j < num_long else 0) for j in range(num_blocks)]

    blocks = [[] for _ in range(num_blocks)]
    index = 0
    for i in range(short_len + (1 if num_long else 0)):
        for j in range(num_blocks):
            if i == short_len and j >= num_long:
                continue
            blocks[j].append(words[index])
            index += 1
    ecc = [[] for _ in range(num_blocks)]
    for i in range(ecc_len):
        for j in range(num_blocks):
            ecc[j].append(words[index])
            index += 1

    stream = words[data_total:data_total + ecc_len * num_blocks]
    for j in range(num_blocks):
        assert len(stream) == ecc_len * num_blocks
        block = blocks[j] + ecc[j]
        assert syndrome_zero(block, ecc_len), "RS syndrome is non-zero"
    assert sum(len(b) for b in blocks) == data_total

    # Concatenate the blocks back in their natural order and parse the header.
    flat = []
    for j in range(num_blocks):
        flat.extend(blocks[j])
    all_bits = []
    for word in flat:
        for shift in range(7, -1, -1):
            all_bits.append((word >> shift) & 1)
    pos = 0

    def take(count):
        nonlocal pos
        value = 0
        for _ in range(count):
            value = (value << 1) | all_bits[pos]
            pos += 1
        return value

    mode = take(4)
    assert mode == 0b0100, "expected byte mode, got %s" % bin(mode)
    length = take(8 if version < 10 else 16)
    payload = bytes(bytearray(take(8) for _ in range(length)))
    return level, mask, payload, stream


class QrTest(unittest.TestCase):
    def test_capacity_table_matches_standard(self):
        for version, expected in BYTE_CAPACITY.items():
            for level, want in zip("LMQH", expected):
                self.assertEqual(
                    qr.byte_capacity(version, level), want,
                    "version %d level %s" % (version, level))

    def test_format_info_known_answers(self):
        for level, bits in FORMAT_MASK0.items():
            self.assertEqual(qr.format_bits(level, 0), bits, level)

    def test_version_info_known_answer(self):
        self.assertEqual(qr.version_bits(7), 0x07C94)
        for version in range(7, 41):
            bits = qr.version_bits(version)
            self.assertEqual(qr.poly_remainder(bits, qr.VERSION_POLYNOMIAL), 0)

    def test_generator_polynomial_known_answer(self):
        self.assertEqual(qr.rs_generator(2), [1, 3, 2])

    def test_rs_example_from_the_standard(self):
        # ISO/IEC 18004 Annex i: 1-M "01234567", 10 EC codewords.
        data = [32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17]
        self.assertEqual(qr.rs_remainder(data, 10),
                         [196, 35, 39, 119, 235, 215, 231, 226, 93, 23])

    def test_alignment_positions_known_table(self):
        expected = {
            1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
            7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50],
            14: [6, 26, 46, 66], 32: [6, 34, 60, 86, 112, 138],
            40: [6, 30, 58, 86, 114, 142, 170],
        }
        for version, want in expected.items():
            self.assertEqual(qr.alignment_positions(version), want, "v%d" % version)

    def test_alignment_geometry_invariants(self):
        for version in range(2, 41):
            positions = qr.alignment_positions(version)
            size = version * 4 + 17
            self.assertEqual(positions[0], 6, "v%d first centre" % version)
            self.assertEqual(positions[-1], size - 7, "v%d last centre" % version)
            self.assertEqual(len(positions), version // 7 + 2, "v%d count" % version)

    def test_geometry(self):
        symbol = qr.encode("http://192.168.1.10:8080")
        size = symbol.size
        self.assertEqual(size, 4 * symbol.version + 17)
        self.assertTrue(symbol.modules[0][0])
        self.assertTrue(symbol.modules[0][6])
        self.assertFalse(symbol.modules[0][7])          # separator
        self.assertTrue(symbol.modules[size - 8][8])     # fixed dark module
        self.assertEqual(symbol.modules[6][8], symbol.modules[8][6])  # timing parity
        fn = function_map(symbol.version)
        free = sum(1 for r in range(size) for c in range(size) if not fn[r][c])
        self.assertEqual(free, qr.raw_data_modules(symbol.version))

    def test_round_trip_short_url(self):
        text = "http://192.168.1.10:8080"
        symbol = qr.encode(text, level="M")
        level, mask, payload, _ = decode(symbol)
        self.assertEqual(payload.decode("utf-8"), text)
        self.assertEqual(level, "M")
        self.assertIn(mask, range(8))

    def test_round_trip_every_level_and_length(self):
        cases = [
            ("http://10.0.0.5:8080", "L"),
            ("http://192.168.1.10:8080/?j=abc123", "M"),
            ("http://192.168.1.10:8080/?j=" + "Z" * 120, "Q"),
            ("x" * 300, "H"),
        ]
        for text, level in cases:
            symbol = qr.encode(text, level=level)
            _, _, payload, _ = decode(symbol)
            self.assertEqual(payload.decode("utf-8"), text,
                             "round trip failed for %d chars at %s" % (len(text), level))

    def test_round_trip_across_versions(self):
        for length in (1, 8, 16, 40, 80, 150, 260, 500, 900, 1500):
            text = "".join(chr(97 + (i % 26)) for i in range(length))
            symbol = qr.encode(text, level="M")
            _, _, payload, _ = decode(symbol)
            self.assertEqual(payload.decode("utf-8"), text, "length %d" % length)

    def test_mask_fixed_still_decodes(self):
        for mask in range(8):
            symbol = qr.encode("http://192.168.1.50:8080/", level="L", mask=mask)
            _, used, payload, _ = decode(symbol)
            self.assertEqual(used, mask)
            self.assertEqual(payload.decode("utf-8"), "http://192.168.1.50:8080/")

    def test_svg_output_is_sized(self):
        symbol = qr.encode("http://192.168.1.10:8080", level="M")
        svg = symbol.to_svg(scale=4, quiet=3)
        want = (symbol.size + 6) * 4
        self.assertIn('width="%d"' % want, svg)
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("</svg>", svg)

    def test_oversized_input_rejected(self):
        with self.assertRaises(ValueError):
            qr.encode("x" * 4000, level="H")


if __name__ == "__main__":
    unittest.main()
