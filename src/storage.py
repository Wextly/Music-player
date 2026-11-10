"""Сохранение и загрузка состояния сессии (player_state.json)."""
import json
import os

from logic import HEX_RE, SORT_MODES, VIZ_STYLES, normalize_hex

import shutil
import tempfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _writable(path) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def pick_data_dir() -> str:
    """Папка для данных приложения. На телефоне её задаёт Flet (FLET_APP_STORAGE_DATA),
    на компьютере берётся папка с программой. Если туда нельзя писать - запасные варианты."""
    candidates = [os.getenv("FLET_APP_STORAGE_DATA"), BASE_DIR,
                  os.path.join(os.path.expanduser("~"), ".wextly"),
                  os.path.join(tempfile.gettempdir(), "wextly")]
    for path in candidates:
        if path and _writable(path):
            return path
    return BASE_DIR


DATA_DIR = pick_data_dir()
MUSIC_DIR = os.path.join(DATA_DIR, "music")
CONFIG_FILE = os.path.join(DATA_DIR, "player_state.json")


def copy_into_music(paths) -> list:
    """Копирует выбранные файлы в память приложения и возвращает новые пути.
    На телефоне путь из окна выбора файла временный, поэтому музыку нужно сохранять у себя.
    Тот же файл (имя и размер) повторно не копируется."""
    os.makedirs(MUSIC_DIR, exist_ok=True)
    result = []
    for src in paths:
        try:
            name = os.path.basename(src) or "track.mp3"
            size = os.path.getsize(src)
            stem, ext = os.path.splitext(name)
            dst, n = os.path.join(MUSIC_DIR, name), 1
            while os.path.exists(dst) and os.path.getsize(dst) != size:
                dst = os.path.join(MUSIC_DIR, f"{stem}_{n}{ext}")
                n += 1
            if not os.path.exists(dst):
                shutil.copyfile(src, dst)
            result.append(dst)
        except OSError as err:
            print(f">>> Не удалось скопировать {src}: {err}")
    return result

DEFAULT_STATE = {
    "last_track": "Трек не выбран",
    "artist": "Неизвестный исполнитель",
    "tracks": {},              # библиотека: id -> данные трека
    "playlists": [],           # [{"id": ..., "name": ..., "tracks": [id, ...]}]
    "queue": [],               # очередь воспроизведения (id треков)
    "queue_name": "",
    "index": 0,                # позиция в очереди
    "position_percent": 0.0,
    "position_ms": 0,
    "shuffle": False,
    "repeat_mode": 0,          # 0 - выкл, 1 - повтор очереди, 2 - повтор трека
    "favorites": [],           # id избранных треков
    "button_radius": 16.0,
    "panel_opacity": 0.65,
    "background": {"type": "color", "value": "#14142b"},   # color | file | url
    "plays": {},               # id -> сколько раз запускался
    "last_played": {},         # id -> время последнего запуска
    "accent": "#7C5CFF",
    "dynamic_accent": False,   # цвет интерфейса из обложки
    "button_opacity": 0.08,    # прозрачность кнопок
    "volume": 1.0,
    "speed": 1.0,
    "visualizer": True,
    "sort_mode": "added",
    "panel_tint": "#000000",   # цвет стеклянных панелей
    "viz_style": "bars",
    "viz_color": "",           # пусто = цвет акцента
    "show_remaining": False,   # справа показывать «сколько осталось»
}


def _num(value, default, lo, hi, cast=float):
    try:
        return max(lo, min(hi, cast(value)))
    except (TypeError, ValueError):
        return default


def _valid_track(d) -> bool:
    if not isinstance(d, dict) or not isinstance(d.get("id"), str):
        return False
    if d.get("source", "local") == "local" and not os.path.exists(d.get("path", "")):
        return False   # локальный файл удалён или перемещён
    return True


def load_state() -> dict:
    state = dict(DEFAULT_STATE)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                state.update(data)
        except (OSError, json.JSONDecodeError) as err:
            print(f">>> Не удалось прочитать {CONFIG_FILE}: {err}")

    # --- миграция со старой версии (один общий плейлист) ---
    legacy = state.pop("playlist", None)
    if not state["tracks"] and isinstance(legacy, list):
        old = [d for d in legacy if _valid_track(d)]
        state["tracks"] = {d["id"]: d for d in old}
        state["queue"] = [d["id"] for d in old]
        if old:
            state["playlists"] = [{"id": "pl_legacy", "name": "Мой плейлист",
                                   "tracks": [d["id"] for d in old]}]
        state["queue_name"] = "Мой плейлист"

    # --- проверка типов ---
    tracks = state["tracks"] if isinstance(state["tracks"], dict) else {}
    state["tracks"] = {k: v for k, v in tracks.items()
                       if _valid_track(v) and v["id"] == k}

    clean_pl = []
    seen = set()
    for pl in state["playlists"] if isinstance(state["playlists"], list) else []:
        if not isinstance(pl, dict) or not isinstance(pl.get("id"), str) \
                or not isinstance(pl.get("name"), str) or pl["id"] in seen:
            continue
        seen.add(pl["id"])
        ids = pl.get("tracks") if isinstance(pl.get("tracks"), list) else []
        clean_pl.append({"id": pl["id"], "name": pl["name"][:60],
                         "tracks": [i for i in ids if i in state["tracks"]]})
    state["playlists"] = clean_pl

    queue = state["queue"] if isinstance(state["queue"], list) else []
    state["queue"] = [i for i in queue if i in state["tracks"]]
    state["queue_name"] = str(state.get("queue_name") or "")
    state["index"] = _num(state["index"], 0, 0, max(0, len(state["queue"]) - 1), int)
    state["position_percent"] = _num(state["position_percent"], 0.0, 0.0, 100.0)
    state["position_ms"] = _num(state["position_ms"], 0, 0, 10**9, int)
    state["button_radius"] = _num(state["button_radius"], 16.0, 0.0, 36.0)
    state["panel_opacity"] = _num(state["panel_opacity"], 0.65, 0.1, 0.95)
    if state["repeat_mode"] not in (0, 1, 2):
        state["repeat_mode"] = 0
    fav = state["favorites"] if isinstance(state["favorites"], list) else []
    state["favorites"] = [i for i in fav if i in state["tracks"]]

    plays = state["plays"] if isinstance(state["plays"], dict) else {}
    state["plays"] = {k: int(v) for k, v in plays.items()
                      if k in state["tracks"] and isinstance(v, (int, float))}
    last = state["last_played"] if isinstance(state["last_played"], dict) else {}
    state["last_played"] = {k: float(v) for k, v in last.items()
                            if k in state["tracks"] and isinstance(v, (int, float))}
    if not (isinstance(state["accent"], str) and HEX_RE.match(state["accent"])):
        state["accent"] = DEFAULT_STATE["accent"]
    state["dynamic_accent"] = bool(state["dynamic_accent"])
    state["visualizer"] = bool(state["visualizer"])
    state["button_opacity"] = _num(state["button_opacity"], 0.08, 0.0, 0.6)
    state["volume"] = _num(state["volume"], 1.0, 0.0, 1.0)
    state["speed"] = _num(state["speed"], 1.0, 0.5, 2.0)
    state["panel_tint"] = normalize_hex(state["panel_tint"]) or "#000000"
    state["viz_color"] = normalize_hex(state["viz_color"]) or ""
    if state["viz_style"] not in [k for k, _ in VIZ_STYLES]:
        state["viz_style"] = "bars"
    state["show_remaining"] = bool(state["show_remaining"])
    if state["sort_mode"] not in [k for k, _ in SORT_MODES]:
        state["sort_mode"] = "added"

    bg = state["background"]
    if not isinstance(bg, dict) or bg.get("type") not in ("color", "file", "url") \
            or not isinstance(bg.get("value"), str):
        bg = dict(DEFAULT_STATE["background"])
    if bg["type"] == "file" and not os.path.exists(bg["value"]):
        bg = dict(DEFAULT_STATE["background"])   # файл фона удалён
    state["background"] = bg
    return state


def save_state(data: dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as err:
        print(f">>> Не удалось сохранить состояние: {err}")
