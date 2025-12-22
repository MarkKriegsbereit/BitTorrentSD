# common.py
import hashlib

BLOCK_SIZE = 512 * 1024  # 512 KB
BUFFER_SIZE = 8192       # Aumentamos un poco el buffer

# Puertos
TRACKER_PORT = 5000

# Comandos
CMD_ANNOUNCE = "ANNOUNCE"
CMD_REQUEST_CHUNK = "REQUEST_CHUNK"
CMD_LIST_FILES = "LIST_FILES"

def calculate_file_hash(filepath):
    sha256 = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while True:
            data = f.read(4096)
            if not data: break
            sha256.update(data)
    return sha256.hexdigest()
