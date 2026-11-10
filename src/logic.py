"""Чистая логика без Flet: сортировка, история, импорт/экспорт, цвета.
Вынесена отдельно, чтобы её можно было проверять тестами."""
import colorsys
import math
import re
import time
import uuid
from urllib.parse import urlparse

SORT_MODES = [("added", "порядок добавления"), ("title", "название"),
              ("artist", "автор"), ("duration", "длительность")]
SPEEDS = [0.75, 1.0, 1.25, 1.5, 2.0]
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


# ---------- сортировка и поиск ----------
def next_sort_mode(mode: str) -> str:
    keys = [k for k, _ in SORT_MODES]
    return keys[(keys.index(mode) + 1) % len(keys)] if mode in keys else keys[0]


def sort_label(mode: str) -> str:
    return dict(SORT_MODES).get(mode, SORT_MODES[0][1])


def sort_tracks(tracks, mode: str) -> list:
    tracks = list(tracks)
    if mode == "title":
        return sorted(tracks, key=lambda t: t.title.casefold())
    if mode == "artist":
        return sorted(tracks, key=lambda t: (t.artist.casefold(), t.title.casefold()))
    if mode == "duration":
        return sorted(tracks, key=lambda t: (t.duration == 0, t.duration))
    return tracks


def filter_tracks(tracks, query: str) -> list:
    q = (query or "").strip().casefold()
    if not q:
        return list(tracks)
    return [t for t in tracks if q in t.title.casefold() or q in t.artist.casefold()]


# ---------- история прослушивания ----------
def record_play(stats: dict, track_id: str, now=None) -> None:
    now = time.time() if now is None else now
    stats["plays"][track_id] = int(stats["plays"].get(track_id, 0)) + 1
    stats["last"][track_id] = now


def recent_ids(stats: dict, valid, limit: int = 30) -> list:
    items = sorted(((ts, tid) for tid, ts in stats["last"].items() if tid in valid),
                   reverse=True)
    return [tid for _, tid in items[:limit]]


def top_ids(stats: dict, valid, limit: int = 30) -> list:
    items = sorted(((n, stats["last"].get(tid, 0), tid)
                    for tid, n in stats["plays"].items() if tid in valid and n > 0),
                   reverse=True)
    return [x[2] for x in items[:limit]]


# ---------- экспорт и импорт плейлистов ----------
def new_playlist_id() -> str:
    return "pl_" + uuid.uuid4().hex[:10]


def build_export(tracks: dict, playlists: list) -> dict:
    used = {i for pl in playlists for i in pl["tracks"]}
    return {
        "app": "wextly", "version": 1,
        "playlists": [{"name": pl["name"], "tracks": list(pl["tracks"])} for pl in playlists],
        "tracks": {i: tracks[i] for i in used if i in tracks},
    }


def merge_import(data, tracks: dict, playlists: list, track_ok=None):
    """Добавляет плейлисты и треки из файла. tracks (id -> dict) и playlists
    изменяются на месте. Возвращает (новых плейлистов, новых треков)."""
    if not isinstance(data, dict) or data.get("app") != "wextly":
        raise ValueError("Это не файл плейлистов Wextly")
    track_ok = track_ok or (lambda d: True)
    incoming = data.get("tracks") if isinstance(data.get("tracks"), dict) else {}
    added_tracks = 0
    for tid, d in incoming.items():
        if isinstance(d, dict) and d.get("id") == tid and track_ok(d) and tid not in tracks:
            tracks[tid] = d
            added_tracks += 1
    names = {pl["name"].casefold() for pl in playlists}
    added_pl = 0
    for pl in data.get("playlists") if isinstance(data.get("playlists"), list) else []:
        if not isinstance(pl, dict):
            continue
        base = str(pl.get("name", "")).strip()[:60]
        if not base:
            continue
        name, n = base, 2
        while name.casefold() in names:
            name, n = f"{base} ({n})", n + 1
        names.add(name.casefold())
        ids = [i for i in (pl.get("tracks") or []) if i in tracks]
        playlists.append({"id": new_playlist_id(), "name": name, "tracks": ids})
        added_pl += 1
    return added_pl, added_tracks


# ---------- ссылки и текст ----------
def is_soundcloud_url(text: str) -> bool:
    t = (text or "").strip()
    if " " in t or not t.lower().startswith(("http://", "https://")):
        return False
    host = (urlparse(t).hostname or "").lower()
    return host == "soundcloud.com" or host.endswith(".soundcloud.com")


def guess_artist_title(title: str, uploader: str = ""):
    """«Artist - Title (Official)» -> ("Artist", "Title")."""
    clean = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", title or "").strip()
    if " - " in clean:
        artist, song = clean.split(" - ", 1)
        return artist.strip(), song.strip()
    return (uploader or "").strip(), clean


def fmt_remaining(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def seek_target(position_ms: int, delta_ms: int, duration_ms: int) -> int:
    target = position_ms + delta_ms
    if duration_ms > 0:
        target = min(target, max(0, duration_ms - 500))
    return max(0, target)


# ---------- цвета ----------
def accent_from_rgb(r: int, g: int, b: int):
    """Яркий акцентный цвет из среднего цвета обложки (None для серых картинок)."""
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    if s < 0.12:
        return None
    s, v = max(s, 0.5), min(max(v, 0.7), 0.95)
    r2, g2, b2 = colorsys.hsv_to_rgb(h, s, v)
    return "#%02X%02X%02X" % (round(r2 * 255), round(g2 * 255), round(b2 * 255))


def darken(hex_color: str, factor: float = 0.35) -> str:
    if not HEX_RE.match(hex_color or ""):
        return "#24124d"
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02X%02X%02X" % (int(r * factor), int(g * factor), int(b * factor))


# ---------- палитра и выбор цвета ----------
PALETTE = [
    "#000000", "#14142b", "#0f172a", "#1e293b", "#2a1740", "#3b1620", "#0e3b30", "#1c1917",
    "#7C5CFF", "#2DA8FF", "#22C58B", "#FF8A3D", "#FF4D8D", "#E5C100", "#EF4444", "#14B8A6",
    "#A855F7", "#F97316", "#84CC16", "#06B6D4", "#EC4899", "#FFFFFF", "#94A3B8", "#FDE68A",
]


def normalize_hex(text):
    """'#abc', 'ABCDEF', '#aabbcc' -> '#AABBCC'; None, если это не цвет."""
    t = (text or "").strip().lstrip("#")
    if len(t) == 3 and re.fullmatch(r"[0-9a-fA-F]{3}", t):
        t = "".join(ch * 2 for ch in t)
    if re.fullmatch(r"[0-9a-fA-F]{6}", t):
        return "#" + t.upper()
    return None


def hex_to_hsv(hex_color: str):
    """-> (оттенок 0-360, насыщенность 0-100, яркость 0-100)."""
    norm = normalize_hex(hex_color) or "#000000"
    r, g, b = (int(norm[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return h * 360, s * 100, v * 100


def hsv_to_hex(h: float, s: float, v: float) -> str:
    r, g, b = colorsys.hsv_to_rgb((h % 360) / 360, max(0, min(100, s)) / 100,
                                  max(0, min(100, v)) / 100)
    return "#%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255))


# ---------- стили эквалайзера ----------
VIZ_STYLES = [("bars", "Полосы"), ("mirror", "Зеркало"), ("capsules", "Капсулы"),
              ("wave", "Волна"), ("dots", "Точки")]
VIZ_SPEC = {   # n - число элементов, w - ширина, gap - промежуток, align - выравнивание
    "bars": dict(n=28, w=5, gap=4, radius=3, align="end"),
    "mirror": dict(n=28, w=5, gap=4, radius=3, align="center"),
    "capsules": dict(n=9, w=20, gap=8, radius=10, align="end"),
    "wave": dict(n=40, w=3, gap=3, radius=2, align="center"),
    "dots": dict(n=28, w=6, gap=4, radius=3, align="center"),
}


def next_viz_style(style: str) -> str:
    keys = [k for k, _ in VIZ_STYLES]
    return keys[(keys.index(style) + 1) % len(keys)] if style in keys else keys[0]


def viz_values(style: str, t: float, rnd):
    """Новые значения для каждого элемента: высота (px) или смещение -1..1 для точек."""
    n = VIZ_SPEC[style]["n"]
    if style == "wave":
        return [6 + 34 * abs(math.sin(t * 3 + i * 0.45)) * (0.6 + 0.4 * rnd.random())
                for i in range(n)]
    if style == "dots":
        return [rnd.uniform(-1, 1) for _ in range(n)]
    if style == "capsules":
        return [rnd.randint(14, 44) for _ in range(n)]
    return [rnd.randint(6, 42) for _ in range(n)]


def viz_idle_value(style: str) -> float:
    return 1.0 if style == "dots" else 5


# ---------- время и следующий трек ----------
def format_remaining_label(position_ms: int, duration_ms: int) -> str:
    left = max(0, duration_ms - position_ms) // 1000
    return f"-{left // 60}:{left % 60:02d}"


def next_queue_index(index: int, size: int, shuffle: bool, repeat_mode: int,
                     chosen=None, rnd=None, ignore_repeat_one=False):
    """Индекс следующего трека или None, если очередь закончится.
    chosen - уже выбранный случайный индекс (чтобы показанный и сыгранный трек совпали)."""
    if size == 0:
        return None
    if repeat_mode == 2 and not ignore_repeat_one:
        return index
    if shuffle and size > 1:
        if chosen is not None and 0 <= chosen < size and chosen != index:
            return chosen
        pool = [i for i in range(size) if i != index]
        return (rnd or __import__("random")).choice(pool)
    if index < size - 1:
        return index + 1
    return 0 if (repeat_mode == 1 or ignore_repeat_one) else None
