import sys
import os

# Asegúrate de que el directorio de tu backend esté en el PATH
sys.path.insert(0, os.path.dirname(__file__))

# Importa el adaptador WSGI y tu aplicación FastAPI
from a2wsgi import ASGIMiddleware
from main import app  # Reemplaza 'main' por el nombre de tu archivo principal (ej. app.py -> de app importar app)

# Passenger buscará la variable 'application'
application = ASGIMiddleware(app)