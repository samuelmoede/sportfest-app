import hashlib
import hmac
import secrets

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    """Erzeugt einen salted PBKDF2-Hash im Format
    "pbkdf2_sha256$<iterationen>$<salt-hex>$<hash-hex>". Nur Standardbibliothek
    (hashlib.pbkdf2_hmac ist OpenSSL-gestuetzt) - keine neue Abhaengigkeit."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS
    )
    return f"{_ALGORITHM}${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Vergleicht password gegen einen von hash_password() erzeugten Hash.
    Zeitkonstanter Vergleich per hmac.compare_digest; jede unerwartete Form
    (leer, falsches Format, fremder Algorithmus) gilt als "passt nicht"."""
    if not password or not stored_hash:
        return False
    parts = stored_hash.split("$")
    if len(parts) != 4:
        return False
    algorithm, iterations_raw, salt, hex_digest = parts
    if algorithm != _ALGORITHM:
        return False
    try:
        iterations = int(iterations_raw)
        salt_bytes = bytes.fromhex(salt)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt_bytes, iterations
    )
    return hmac.compare_digest(digest.hex(), hex_digest)
