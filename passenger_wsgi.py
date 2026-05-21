import sys
import os

# Añade el directorio actual al path de Python
sys.path.insert(0, os.path.dirname(__file__))

from a2wsgi import ASGIMiddleware
from main import app  # Importa la instancia 'app' desde tu archivo main.py

# Phusion Passenger busca esta variable exacta: 'application'
application = ASGIMiddleware(app)