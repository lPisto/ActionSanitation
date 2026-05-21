import os
import sys

# Añadir el directorio actual al path de Python para que encuentre 'app' y 'main'
sys.path.insert(0, os.path.dirname(__file__))

from main import app as asgi_app
from a2wsgi import ASGIMiddleware

# Phusion Passenger en cPanel busca por defecto un objeto llamado 'application' que sea WSGI
application = ASGIMiddleware(asgi_app)
