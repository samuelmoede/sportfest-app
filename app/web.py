from pathlib import Path

from fastapi.templating import Jinja2Templates


APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


def get_app_version():
    try:
        with open(ROOT_DIR / "VERSION", "r", encoding="utf-8") as version_file:
            return version_file.read().strip()
    except FileNotFoundError:
        return "dev"


def get_style_version():
    style_path = APP_DIR / "static" / "style.css"
    try:
        return str(int(style_path.stat().st_mtime))
    except FileNotFoundError:
        return get_app_version()


templates.env.globals["app_version"] = get_app_version
templates.env.globals["style_version"] = get_style_version