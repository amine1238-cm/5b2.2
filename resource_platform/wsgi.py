from .application import create_app
from .config import Settings
application = create_app(Settings.from_env())
