import urllib.request
import urllib.error
import sys

try:
    response = urllib.request.urlopen('http://localhost:8000/api/products?limit=4&sort=-created')
    print(response.read().decode())
except urllib.error.HTTPError as e:
    print('Error:', e.code, file=sys.stderr)
    print(e.read().decode(), file=sys.stderr)
except Exception as e:
    print('Error:', str(e))
