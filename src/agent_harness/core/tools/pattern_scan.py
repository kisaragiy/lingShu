"""
Pattern scanner — identifies common crypto/anti-reverse patterns in binary data.

I call this tool when I need to recognize what kind of encryption or
offuscation I'm looking at during reverse engineering.
"""

import json, os, sys

# Known crypto constants for pattern matching
_AES_SBOX = bytes([
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
])

_BASE64_ALPHABET = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_BASE64URL_ALPHABET = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"

_XOR_END_MARKERS = [
    bytes([0x00] * 4),  # null padding after XOR
    bytes([0xFF] * 4),  # 0xFF padding
]


def _find_aes_sbox(data: bytes) -> list[int]:
    """Find AES S-box byte sequence in data."""
    offsets = []
    for i in range(len(data) - 255):
        if data[i:i+16] == _AES_SBOX[:16]:
            offsets.append(i)
        elif data[i:i+16] == _AES_SBOX[16:32]:
            offsets.append(i)
    return offsets[:5]


def _find_base64_table(data: bytes) -> bool:
    """Check if the data CONTAINS a full base64 alphabet (likely lookup table)."""
    # Full base64 alphabet is 64 bytes; check if all chars present somewhere
    for alphabet in [_BASE64_ALPHABET, _BASE64URL_ALPHABET]:
        found = sum(1 for c in alphabet if c in data)
        if found >= 60:  # 60+ / 64 = very likely
            return True
    return False


def _estimate_entropy(data: bytes) -> float:
    """Shannon entropy — high entropy suggests encrypted/compressed data."""
    if not data:
        return 0.0
    counts = {}
    for b in data:
        counts[b] = counts.get(b, 0) + 1
    n = len(data)
    entropy = -sum(c / n * __import__("math").log2(c / n) for c in counts.values())
    return round(entropy, 2)


def _tool_pattern_scan(data: str, data_format: str = "hex") -> str:
    """
    Scan binary data for known crypto/anti-reverse patterns.

    I call this when looking at unknown binary data during reverse engineering.
    It identifies AES S-box, base64 lookup tables, XOR patterns, and entropy.

    Args:
        data: hex string or raw bytes representation
        data_format: "hex" (default) or "raw" or "base64"

    Returns:
        JSON with detected patterns and entropy analysis.
    """
    try:
        if data_format == "hex":
            clean = data.replace(" ", "").replace("\
", "").replace("0x", "").replace("\\\\x", "")
            raw = bytes.fromhex(clean)
        elif data_format == "base64":
            import base64
            raw = base64.b64decode(data)
        else:
            raw = data.encode("latin-1")
    except Exception as e:
        return json.dumps({"ok": False, "error": f"decode failed: {e}"})

    results = {
        "ok": True,
        "length": len(raw),
        "entropy": _estimate_entropy(raw),
        "is_high_entropy": _estimate_entropy(raw) > 7.0,
    }

    # AES S-box detection
    sbox_offsets = _find_aes_sbox(raw)
    if sbox_offsets:
        results["aes_sbox"] = {"detected": True, "offsets": sbox_offsets[:3]}

    # Base64 table detection
    if _find_base64_table(raw):
        results["base64_table"] = {"detected": True}

    # Check for repeat patterns (XOR key candidate)
    if len(raw) >= 16:
        for period in [1, 2, 4, 8, 16]:
            block = raw[:period]
            if len(block) >= 2 and raw.count(block) >= len(raw) // period * 0.6:
                results["xor_candidate"] = {
                    "period": period,
                    "key_bytes": list(block),
                    "key_hex": block.hex(),
                }
                break

    # Summary for quick reading
    patterns = []
    if "aes_sbox" in results:
        patterns.append("AES S-box")
    if "base64_table" in results:
        patterns.append("base64 lookup table")
    if "xor_candidate" in results:
        patterns.append(f"XOR (period={results['xor_candidate']['period']}, key={results['xor_candidate']['key_hex']})")
    if results["is_high_entropy"] and not patterns:
        patterns.append("high entropy — likely encrypted/compressed")

    results["patterns"] = patterns
    results["summary"] = ", ".join(patterns) if patterns else "no known patterns detected"

    return json.dumps(results, indent=2, ensure_ascii=False)


from .registry import register_tool
register_tool("pattern_scan", _tool_pattern_scan, {
    "description": "🕵️ 加密模式扫描 — 识别二进制数据中的 AES S-box、base64 表、XOR 周期、熵值。我逆向时遇到未知数据就调这个。",
    "properties": {
        "data": {"type": "string", "description": "hex 字符串（去空格/0x）或 raw/base64"},
        "data_format": {"type": "string", "description": "hex / raw / base64", "default": "hex"},
    },
}, privilege="read-only")