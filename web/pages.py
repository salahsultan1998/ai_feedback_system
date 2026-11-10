import secrets

from flask import Blueprint, abort, current_app, render_template, session

from feedback.config import get_game, list_games

pages = Blueprint("pages", __name__)


@pages.before_request
def prepare_session():
    session.setdefault("csrf", secrets.token_hex(24))


@pages.get("/")
def home():
    return render_template("index.html", games=list_games(current_app.config["PROJECT_ROOT"]))


@pages.get("/games/<code>")
def game(code):
    try:
        choice = get_game(code, current_app.config["PROJECT_ROOT"])
    except ValueError:
        abort(404)
    token = current_app.extensions["workspaces"].create(session["csrf"]) if choice["enabled"] else None
    return render_template("game.html", game=choice, workspace_token=token)
