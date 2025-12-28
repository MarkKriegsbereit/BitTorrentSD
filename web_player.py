import os
from flask import Flask, render_template_string, send_from_directory

app = Flask(__name__)

# CONFIGURACIÓN
# Asegúrate que este puerto coincida con el de tu peer_node
PEER_FOLDER = "peer_9000"  

@app.route('/')
def index():
    # 1. Escanear la carpeta en busca de videos MP4
    videos = []
    if os.path.exists(PEER_FOLDER):
        files = os.listdir(PEER_FOLDER)
        for f in files:
            if f.endswith(".mp4"):
                # Verificamos si ya se descargó completo (si no existe el .progress)
                # O simplemente lo mostramos.
                path = os.path.join(PEER_FOLDER, f)
                size_mb = os.path.getsize(path) / (1024 * 1024)
                videos.append({"name": f, "size": f"{size_mb:.2f} MB"})
    
    # 2. HTML simple incrustado (para no crear plantillas aparte)
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Mi P2P Player</title>
        <style>
            body { font-family: sans-serif; background: #222; color: #fff; padding: 20px; }
            .video-card { background: #333; padding: 15px; margin: 10px 0; border-radius: 8px; }
            a { color: #4da6ff; text-decoration: none; font-size: 1.2em; }
            .status { font-size: 0.8em; color: #aaa; }
        </style>
    </head>
    <body>
        <h1>📺 Videos Descargados</h1>
        {% for v in videos %}
            <div class="video-card">
                <a href="/play/{{ v.name }}">{{ v.name }}</a>
                <p class="status">Tamaño: {{ v.size }}</p>
                <video width="320" height="240" controls>
                    <source src="/play/{{ v.name }}" type="video/mp4">
                    Tu navegador no soporta video.
                </video>
            </div>
        {% else %}
            <p>No hay videos descargados en {{ folder }}</p>
        {% endfor %}
    </body>
    </html>
    """
    return render_template_string(html, videos=videos, folder=PEER_FOLDER)

@app.route('/play/<path:filename>')
def play_video(filename):
    # Esta función sirve el archivo de video real para que el navegador lo reproduzca
    return send_from_directory(PEER_FOLDER, filename)

if __name__ == '__main__':
    # Ejecutamos en el puerto 8080 para no chocar con el Peer (9000) ni el Tracker (5000)
    print(f"🚀 Interfaz Web activa en: http://localhost:8080")
    print(f"📂 Leyendo carpeta: {PEER_FOLDER}")
    app.run(host='0.0.0.0', port=8080)