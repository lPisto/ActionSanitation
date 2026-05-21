import os
import sys

BASE_DIR = os.path.dirname(__file__)
sys.path.insert(0, BASE_DIR)

from a2wsgi import ASGIMiddleware
from main import app as fastapi_app

application = ASGIMiddleware(fastapi_app)