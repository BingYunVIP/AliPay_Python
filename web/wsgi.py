from .app import create_app
from .runtime import load_runtime

app = create_app(load_runtime())
