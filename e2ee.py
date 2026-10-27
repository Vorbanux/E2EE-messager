import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import dh
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


class E2EE:
    def __init__(self):
        self.private_key = X25519PrivateKey.generate()
        self.public_key  = self.private_key.public_key()

    def join(self, peer_public_key):
        shared_secret = self.private_key.exchange(peer_public_key)
        self.aes_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"e2ee-v1-aes-key"
        ).derive(shared_secret)
        return self.aes_key
    
    def encoder(self, plaintext: str) -> bytes:
        aesgcm = AESGCM(self.aes_key) # AES-256-GCM object with a key
        nonce  = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode('utf-8'), None)
        return nonce + ciphertext

    def decoder(self, encrypted_payload: bytes) -> str:
        nonce = encrypted_payload[:12]
        ciphertext = encrypted_payload[12:]
        aesgcm = AESGCM(self.aes_key)
        try:
            plain_text = aesgcm.decrypt(nonce, ciphertext, None)
            return plain_text.decode("utf-8")
        except InvalidTag:
            raise ValueError("Decryption failed: message was tempered with or corrupted")

if __name__ == "__main__":
    A = E2EE()
    B = E2EE()
    A.join(B.public_key)
    B.join(A.public_key)
