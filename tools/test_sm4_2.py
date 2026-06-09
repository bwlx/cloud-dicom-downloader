"""Test SM4 with CBC mode on the key parameter."""
import json, base64
from sm4 import SM4Key

# Hospital-specific SM4 key from config
sm4_key_str = "jEeT04X1yLKK4NBVsqSwgvxkgURV645U"
key_bytes = sm4_key_str.encode('utf-8')[:16]  # 16 bytes

key_b64 = "gROpvN2Wm3uXlZc6G3CN0Bp6KWe64P/tZeCwxIgz4rbU3baIJw6ghspwQlCtEekj2tcyLrKKNhiqDaVur7emcQ=="
raw_key = base64.b64decode(key_b64)

# SM4 ECB mode (each block independently)
print("=== SM4 ECB ===")
sm4 = SM4Key(key_bytes)
result = b""
for i in range(0, len(raw_key), 16):
    result += sm4.decrypt(raw_key[i:i+16])
print(f"Raw: {result}")

# SM4 CBC mode - need to implement manually
print("\n=== SM4 CBC (IV = key reversed or zeros) ===")
for iv_label, iv in [
    ("zeros", b'\x00' * 16),
    ("key_reversed", key_bytes[::-1]),
    ("key", key_bytes),
    ("ones", b'\x01' * 16),
]:
    try:
        result = b""
        prev = iv
        for i in range(0, len(raw_key), 16):
            block = raw_key[i:i+16]
            decrypted = sm4.decrypt(block)
            # XOR with previous ciphertext (CBC)
            plain = bytes(a ^ b for a, b in zip(decrypted, prev))
            result += plain
            prev = block
        print(f"IV={iv_label}: {result}")
        for enc in ['utf-8', 'gbk']:
            try:
                print(f"  {enc}: {result.decode(enc)}")
                break
            except:
                pass
    except Exception as e:
        print(f"IV={iv_label}: FAILED - {e}")

# Also try: maybe the key needs different truncation
print("\n=== Different key lengths ===")
for length in [16, 24, 32]:
    k = key_bytes[:length] if length <= len(key_bytes) else key_bytes + b'\x00' * (length - len(key_bytes))
    if len(k) == 16:
        sm4 = SM4Key(k)
        result = b""
        for i in range(0, len(raw_key), 16):
            result += sm4.decrypt(raw_key[i:i+16])
        print(f"Key len {length}: {result[:50]}...")
