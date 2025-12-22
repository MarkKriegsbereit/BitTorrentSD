# file_manager.py
import os
import json
import math
from common import BLOCK_SIZE

class FileManager:
    def __init__(self, base_path, filename, total_size, is_seeder=False):
        self.base_path = base_path
        # Ruta completa: ./peer_6000/video.mp4
        self.filepath = os.path.join(base_path, filename)
        self.log_file = os.path.join(base_path, f"{filename}.progress")
        self.is_seeder = is_seeder
        self.total_size = total_size
        
        # Calcular chunks
        self.total_chunks = math.ceil(self.total_size / BLOCK_SIZE)
        
        if is_seeder:
            self.bitfield = [True] * self.total_chunks
        else:
            self.bitfield = [False] * self.total_chunks
            self.load_progress()

    def load_progress(self):
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r') as f:
                    data = json.load(f)
                    for idx in data.get("downloaded", []):
                        if idx < self.total_chunks:
                            self.bitfield[idx] = True
            except: pass

    def save_progress(self):
        if not self.is_seeder:
            downloaded = [i for i, x in enumerate(self.bitfield) if x]
            with open(self.log_file, 'w') as f:
                json.dump({"downloaded": downloaded}, f)

    def write_chunk(self, index, data):
        # Asegurar que existe la carpeta
        if not os.path.exists(self.base_path):
            os.makedirs(self.base_path)

        mode = 'r+b' if os.path.exists(self.filepath) else 'wb'
        with open(self.filepath, mode) as f:
            f.seek(index * BLOCK_SIZE)
            f.write(data)
        self.bitfield[index] = True
        self.save_progress()

    def read_chunk(self, index):
        if not os.path.exists(self.filepath): return None
        if index >= self.total_chunks or not self.bitfield[index]: return None
        
        with open(self.filepath, 'rb') as f:
            f.seek(index * BLOCK_SIZE)
            return f.read(BLOCK_SIZE)

    def get_missing_chunks(self):
        return [i for i, x in enumerate(self.bitfield) if not x]

    def get_progress_percentage(self):
        if self.total_chunks == 0: return 0
        return round((sum(self.bitfield) / self.total_chunks) * 100, 2)
