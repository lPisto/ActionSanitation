import sys
import os
import re

# Añade el directorio actual al path de Python
sys.path.insert(0, os.path.dirname(__file__))

from a2wsgi import ASGIMiddleware
from main import app  # Importa la instancia 'app' desde tu archivo main.py

_wsgi_app = None

# Envolver la aplicación para gestionar la subcarpeta correctamente en cPanel
def application(environ, start_response):
    global _wsgi_app
    if _wsgi_app is None:
        _wsgi_app = ASGIMiddleware(app)

    script_name = "/actionsanitation"
    path_info = environ.get('PATH_INFO', '')
    
    # Si PATH_INFO está vacío, intentar obtenerlo de REQUEST_URI
    if not path_info:
        path_info = environ.get('REQUEST_URI', '').split('?')[0]

    # Limpiar el nombre del script de la ruta si Apache no lo hizo
    if path_info.startswith(script_name):
        path_info = path_info[len(script_name):]
        
    # Evitar rutas vacías o con doble slash (ej: //api/health)
    path_info = re.sub(r'^/+', '/', path_info)
    environ['PATH_INFO'] = path_info if path_info else "/"
    environ['SCRIPT_NAME'] = script_name
        
    return _wsgi_app(environ, start_response)