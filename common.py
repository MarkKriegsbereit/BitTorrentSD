# common.py
import hashlib
import socket

BLOCK_SIZE = 1024 * 1024*5  # 5 MB
BUFFER_SIZE = 65536       # 64 KB

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
    Intenta obtener la IP de la interfaz LAN real.
    """
    try:
        # Metodo 1: Conectar a un DNS público (requiere internet)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        IP = s.getsockname()[0]
        s.close()
    except:
        try:
            # Metodo 2: Obtener el nombre del host (funciona en muchas LANs sin internet)
            IP = socket.gethostbyname(socket.gethostname())
        except:
            IP = '127.0.0.1'
    
    # Si detecta localhost, avisamos (esto rompe el Bridged mode)
    if IP.startswith("127."):
        print("⚠️ ADVERTENCIA: Se detectó IP local (127.x.x.x).")
        print("   Si estás en modo Bridged, esto impedirá conexiones externas.")
        
    return IP
