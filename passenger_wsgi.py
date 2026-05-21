import sys
import os

# Añade el directorio actual al path de Python
sys.path.insert(0, os.path.dirname(__file__))

from a2wsgi import ASGIMiddleware
from main import app  # Importa la instancia 'app' desde tu archivo main.py

# Envolver la aplicación para gestionar la subcarpeta correctamente en cPanel
def wsgi_app(environ, start_response):
    script_name = "/actionsanitation"
    path_info = environ.get('PATH_INFO', '')
    
    if path_info.startswith(script_name):
        environ['PATH_INFO'] = path_info[len(script_name):] or "/"
        environ['SCRIPT_NAME'] = script_name
        
    return ASGIMiddleware(app)(environ, start_response)

application = wsgi_app