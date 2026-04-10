import os
from flask import Flask
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message_category = "warning"

def create_app():
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)
    db_path = os.path.join(app.instance_path, "technoplus_pos.db")
    app.config.update(
        SECRET_KEY="change-this-secret-key",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_path}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )

    db.init_app(app)
    login_manager.init_app(app)

    from .models import User
    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from .routes import register_routes
    register_routes(app)

    with app.app_context():
        from . import models
        db.create_all()
        from .utils import seed_defaults
        seed_defaults()

    return app
