from flask_sqlalchemy import SQLAlchemy
import os
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_caching import Cache
from flask_socketio import SocketIO

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
limiter = Limiter(key_func=get_remote_address)
cache = Cache()
socketio = SocketIO()


ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}


UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "imagensprodutos")