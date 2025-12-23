# common.py
import hashlib
import socket

BLOCK_SIZE = 512 * 1024  # 512 KB
BUFFER_SIZE = 8192       # Aumentamos un poco el buffer

# Puertos
TRACKER_PORT = 5000

# Comandos
CMD_ANNOUNCE = "ANNOUNCE"
CMD_REQUEST_CHUNK = "REQUEST_CHUNK"
CMD_LIST_FILES = "LIST_FILES"
CMD_EXIT_SWARM = "EXIT_SWARM"
CMD_GET_METADATA = "GET_METADATA"

def calculate_file_hash(filepath):
    sha256 = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while True:
            data = f.read(4096)
            if not data: break
            sha256.update(data)
    return sha256.hexdigest()
    
    
def get_local_ip():
    """
    Detecta la IP real de la máquina en la red (ej. 192.168.1.50).
    No conecta a internet, solo consulta la ruta de salida.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Intentamos conectar a una IP pública (Google DNS)
        # No envía paquetes reales, solo calcula qué interfaz usaría.
        s.connect(('8.8.8.8', 80))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP
