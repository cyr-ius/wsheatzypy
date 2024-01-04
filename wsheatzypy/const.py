"""Constants for the Heatzy component."""
from yarl import URL as yurl

HEATZY_APPLICATION_ID = "c70a66ff039d41b4a220e198b0fcc8b3"
HEATZY_API_URL = "https://euapi.gizwits.com/app"
WS_PING_INTERVAL = 30
WS_HOST = "eusandbox.gizwits.com"
WS_PORT = 8080
WS_URL = yurl.build(scheme="ws", host=WS_HOST, port=WS_PORT, path="/ws/app/v1")
TIMEOUT = 120
WSS_PORT = 8880
WSS_URL = yurl.build(scheme="wss", host=WS_HOST, port=WSS_PORT, path="/ws/app/v1")
