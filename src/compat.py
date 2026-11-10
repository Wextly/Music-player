"""Помощники для совместимости с разными версиями Flet."""
import base64

import flet as ft

CENTER = ft.Alignment(0, 0)
SPACE_BETWEEN = ft.MainAxisAlignment.SPACE_BETWEEN
SPACE_AROUND = ft.MainAxisAlignment.SPACE_AROUND
BUTTON = getattr(ft, "Button", None) or ft.ElevatedButton


def radius(r: float):
    return ft.BorderRadius(r, r, r, r)


def glass_blur():
    """Размытие фона под панелью (эффект матового стекла)."""
    try:
        return ft.Blur(12, 12)
    except Exception:
        return None


# ----- иконки -----
def icon_value(name: str):
    icons = getattr(ft, "Icons", None) or getattr(ft, "icons", None)
    return getattr(icons, name.upper(), name) if icons else name


def set_icon(icon: ft.Icon, name: str):
    value = icon_value(name)
    if hasattr(icon, "icon"):
        icon.icon = value
    else:
        icon.name = value


def make_icon(name: str, **kwargs):
    value = icon_value(name)
    try:
        return ft.Icon(icon=value, **kwargs)
    except TypeError:
        return ft.Icon(name=value, **kwargs)


# ----- окно, сервисы, диалоги -----
def set_window_size(page: ft.Page, width: int, height: int):
    try:
        page.window.width = width
        page.window.height = height
    except Exception:
        try:
            page.window_width = width
            page.window_height = height
        except Exception:
            pass          # на телефоне размер окна не задаётся


def add_service(page: ft.Page, service):
    try:
        page.services.append(service)
    except AttributeError:
        page.overlay.append(service)


def open_dialog(page: ft.Page, dlg):
    if hasattr(page, "show_dialog"):
        page.show_dialog(dlg)
    elif hasattr(page, "open"):
        page.open(dlg)
    else:
        page.dialog = dlg
        dlg.open = True
        page.update()


def close_dialog(page: ft.Page, dlg):
    if hasattr(page, "pop_dialog"):
        page.pop_dialog()
    elif hasattr(page, "close"):
        page.close(dlg)
    else:
        dlg.open = False
        page.update()


def toast(page: ft.Page, text: str):
    bar = ft.SnackBar(ft.Text(text))
    if hasattr(page, "show_dialog") or hasattr(page, "open"):
        open_dialog(page, bar)
    else:
        page.snack_bar = bar
        bar.open = True
        page.update()


# ----- время -----
def to_ms(value) -> int:
    if value is None:
        return 0
    if hasattr(value, "in_milliseconds"):
        return int(value.in_milliseconds)
    if hasattr(value, "total_seconds"):
        return int(value.total_seconds() * 1000)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def make_duration(ms: int):
    try:
        return ft.Duration(milliseconds=int(ms))
    except TypeError:
        return int(ms) // 1000


def fmt_time(ms: int) -> str:
    seconds = max(0, int(ms // 1000))
    return f"{seconds // 60}:{seconds % 60:02d}"


# ----- фон -----
def _cover():
    for name in ("BoxFit", "ImageFit"):
        enum = getattr(ft, name, None)
        if enum is not None and hasattr(enum, "COVER"):
            return enum.COVER
    return "cover"


def make_bg_image(source: str, is_url: bool, width=None, height=None):
    """Картинка/GIF на весь экран (обрезается по краям, как cover).
    Файл читается и передаётся как base64: так работает на всех платформах
    и GIF остаётся анимированным. Размеры нужны явно, иначе Flet рисует
    картинку в исходном размере."""
    kw = {"fit": _cover(), "expand": True}
    if width:
        kw["width"] = width
    if height:
        kw["height"] = height
    if is_url:
        return ft.Image(src=source, **kw)
    with open(source, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    try:
        return ft.Image(src_base64=b64, **kw)   # старые версии Flet
    except TypeError:
        return ft.Image(src=b64, **kw)          # новые версии Flet


COVER = _cover()


def border_all(width: float, color: str):
    for owner in (getattr(ft, "Border", None), getattr(ft, "border", None)):
        if owner is not None and hasattr(owner, "all"):
            return owner.all(width, color)
    return None


def smooth(ms: int = 160):
    """Плавное изменение размера (для эквалайзера); None, если версия не поддерживает."""
    try:
        return ft.Animation(ms, ft.AnimationCurve.EASE_OUT)
    except Exception:
        return None


def make_switcher(content, ms: int = 250, expand: bool = False):
    """Контейнер, который плавно «растворяет» старое содержимое в новое."""
    try:
        return ft.AnimatedSwitcher(
            content=content, transition=ft.AnimatedSwitcherTransition.FADE,
            duration=ms, reverse_duration=ms, expand=expand)
    except Exception:
        return ft.Container(content=content, expand=expand)


def make_safe(content):
    """Отступы под вырез камеры и системные панели телефона."""
    try:
        return ft.SafeArea(content=content, expand=True)
    except Exception:
        return content
