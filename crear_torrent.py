# crear_torrent.py
import json
import os
import sys
from common import calculate_file_hash, BLOCK_SIZE

def create_torrent(file_path, tracker_ip="127.0.0.1", tracker_port=5000):
    if not os.path.exists(file_path):
        print(f"Error: El archivo {file_path} no existe.")
        return

    print(f"Generando metadatos para {file_path}...")
    file_size = os.path.getsize(file_path)
    file_name = os.path.basename(file_path)
    file_hash = calculate_file_hash(file_path)
    
    torrent_data = {
        "filename": file_name,
        "filesize": file_size,
        "piece_size": BLOCK_SIZE,
        "filehash": file_hash,
        "trackers": [(tracker_ip, tracker_port)]
    }

    output_name = f"{file_name}.json"
    with open(output_name, "w") as f:
        json.dump(torrent_data, f, indent=4)
    
    print(f"¡ÉXITO! Archivo '{output_name}' creado.")
    print("Distribuye este archivo a los clientes (peers).")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 crear_torrent.py <ruta_archivo_mp4>")
    else:
        create_torrent(sys.argv[1])
