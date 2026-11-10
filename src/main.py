"""
Wextly Music Player (Flet 0.70+).

Запуск:  flet run main.py
Экраны:  Плеер · Поиск · Библиотека · Настройки
Данные:  библиотека (все треки) -> плейлисты (ссылаются на треки) -> очередь (играет сейчас)
"""
import asyncio
import json
import os
import random
import shutil
import time

import flet as ft
import flet_audio as fta

import logic
from compat import (
    CENTER, COVER, SPACE_AROUND, SPACE_BETWEEN, add_service, border_all, close_dialog,
    fmt_time, glass_blur, icon_value, make_bg_image, make_duration, make_icon,
    make_safe, make_switcher, open_dialog, radius, set_icon, set_window_size, smooth, to_ms, toast,
)
from sources import (
    NO_ARTIST, PILImage, Track, TrackRepository, dominant_color, fetch_lyrics, read_duration,
)
from storage import DATA_DIR, copy_into_music, load_state, save_state

NO_TRACK = "Трек не выбран"
BG_DIR = os.path.join(DATA_DIR, "backgrounds")
EXPORT_DIR = os.path.join(DATA_DIR, "exports")
COLOR_CHOICES = ["#14142b", "#0f172a", "#1e293b", "#2a1740", "#3b1620", "#0e3b30",
                 "#1c1917", "#000000", "#312e81", "#134e4a", "#4a044e", "#7f1d1d"]
ACCENTS = ["#7C5CFF", "#2DA8FF", "#22C58B", "#FF8A3D", "#FF4D8D", "#E5C100",
           "#EF4444", "#14B8A6", "#A855F7", "#84CC16", "#06B6D4", "#F472B6"]
SOURCE_ICON = {"local": "music_note", "telegram": "cloud", "soundcloud": "graphic_eq"}
ALL, FAV, RECENT, TOP = "__all__", "__fav__", "__recent__", "__top__"
SPECIAL = {ALL: "Все треки", FAV: "Избранное", RECENT: "Недавно играло",
           TOP: "Часто слушаемое"}
SPECIAL_ICON = {ALL: "library_music", FAV: "favorite", RECENT: "history", TOP: "trending_up"}
SLEEP_OPTIONS = [15, 30, 45, 60, 90]


def main(page: ft.Page):
    page.title = "Wextly Music Player"
    page.theme_mode = ft.ThemeMode.DARK
    page.vertical_alignment = ft.MainAxisAlignment.CENTER
    page.horizontal_alignment = ft.CrossAxisAlignment.CENTER
    page.padding = 0
    page.bgcolor = "#14142b"
    set_window_size(page, 420, 820)
    platform = str(getattr(page, "platform", "")).lower()
    IS_MOBILE = "android" in platform or "ios" in platform

    saved = load_state()
    repo = TrackRepository(DATA_DIR)
    library = {tid: Track.from_dict(d) for tid, d in saved["tracks"].items()}

    p = {
        "tracks": library, "playlists": saved["playlists"],
        "queue": [library[i] for i in saved["queue"] if i in library],
        "queue_name": saved["queue_name"], "index": saved["index"],
        "favorites": set(saved["favorites"]),
        "stats": {"plays": dict(saved["plays"]), "last": dict(saved["last_played"])},
        "is_playing": False, "started": False, "need_load": bool(saved["queue"]),
        "load_id": 0, "seek_token": 0, "duration_ms": 0, "position_ms": saved["position_ms"],
        "resume_ms": 0, "dragging": False, "tick": 0,
        "shuffle": bool(saved["shuffle"]), "repeat_mode": saved["repeat_mode"],
        "button_radius": saved["button_radius"], "panel_opacity": saved["panel_opacity"],
        "button_opacity": saved["button_opacity"], "background": saved["background"],
        "accent": saved["accent"], "accent_now": saved["accent"],
        "dynamic_accent": saved["dynamic_accent"], "volume": saved["volume"],
        "speed": saved["speed"], "visualizer": saved["visualizer"], "viz_token": 0,
        "sort_mode": saved["sort_mode"], "view": 0, "lib_view": None, "lib_query": "",
        "sleep_until": None, "sleep_eot": False, "sleep_token": 0, "results": [],
        "panel_tint": saved["panel_tint"], "viz_style": saved["viz_style"],
        "viz_color": saved["viz_color"], "show_remaining": saved["show_remaining"],
        "shuffle_next_id": None, "anchor": None,
        "art": int(max(200, min(300, (page.width or 420) - 72))),
    }
    glass_panels = []     # панели с настраиваемой прозрачностью
    ghost_buttons = []    # постоянные кнопки: (контейнер, акцентная ли)
    accent_sliders = []

    def spawn(fn, *args):
        """Запуск async-функции из обычного (синхронного) обработчика."""
        if hasattr(page, "run_task"):
            page.run_task(fn, *args)
        else:
            asyncio.ensure_future(fn(*args))

    # ======================= Данные: библиотека, плейлисты, история =======================
    def current_track():
        if p["queue"] and 0 <= p["index"] < len(p["queue"]):
            return p["queue"][p["index"]]
        return None

    def register(tracks):
        for t in tracks:
            p["tracks"].setdefault(t.id, t)

    def get_playlist(pid):
        return next((pl for pl in p["playlists"] if pl["id"] == pid), None)

    def playlist_name(pid) -> str:
        if pid in SPECIAL:
            return SPECIAL[pid]
        pl = get_playlist(pid)
        return pl["name"] if pl else ""

    def tracks_of(pid) -> list:
        if pid == ALL:
            return list(p["tracks"].values())
        if pid == FAV:
            return [t for t in p["tracks"].values() if t.id in p["favorites"]]
        if pid == RECENT:
            return [p["tracks"][i] for i in logic.recent_ids(p["stats"], p["tracks"])]
        if pid == TOP:
            return [p["tracks"][i] for i in logic.top_ids(p["stats"], p["tracks"])]
        pl = get_playlist(pid)
        return [p["tracks"][i] for i in pl["tracks"] if i in p["tracks"]] if pl else []

    def create_playlist(name: str) -> dict:
        pl = {"id": logic.new_playlist_id(), "name": name.strip()[:60], "tracks": []}
        p["playlists"].append(pl)
        return pl

    def add_to_playlist(pid, track) -> bool:
        register([track])
        pl = get_playlist(pid)
        if pl is None or track.id in pl["tracks"]:
            return False
        pl["tracks"].append(track.id)
        return True

    def persist():
        t = current_track()
        save_state({
            "last_track": t.title if t else NO_TRACK,
            "artist": t.artist if t else NO_ARTIST,
            "tracks": {tid: tr.to_dict() for tid, tr in p["tracks"].items()},
            "playlists": p["playlists"],
            "queue": [tr.id for tr in p["queue"]], "queue_name": p["queue_name"],
            "index": p["index"], "position_percent": progress_slider.value,
            "position_ms": p["position_ms"], "shuffle": p["shuffle"],
            "repeat_mode": p["repeat_mode"], "favorites": sorted(p["favorites"]),
            "button_radius": p["button_radius"], "panel_opacity": p["panel_opacity"],
            "button_opacity": p["button_opacity"], "background": p["background"],
            "plays": p["stats"]["plays"], "last_played": p["stats"]["last"],
            "accent": p["accent"], "dynamic_accent": p["dynamic_accent"],
            "volume": p["volume"], "speed": p["speed"], "visualizer": p["visualizer"],
            "sort_mode": p["sort_mode"], "panel_tint": p["panel_tint"],
            "viz_style": p["viz_style"], "viz_color": p["viz_color"],
            "show_remaining": p["show_remaining"],
        })

    # ======================= Оформление: прозрачность, акцент =======================
    def panel_color():
        return ft.Colors.with_opacity(p["panel_opacity"], p["panel_tint"])

    def nav_color():
        return ft.Colors.with_opacity(min(0.95, p["panel_opacity"] + 0.1), p["panel_tint"])

    def bar_color():
        return p["viz_color"] or p["accent_now"]

    def card_fill():
        return ft.Colors.with_opacity(min(0.25, p["button_opacity"] * 0.9 + 0.02), "white")

    def btn_fill(accent=False):
        return ft.Colors.with_opacity(p["button_opacity"], p["accent_now"] if accent else "white")

    def btn_edge(accent=False):
        col = ft.Colors.with_opacity(0.65, p["accent_now"]) if accent \
            else ft.Colors.with_opacity(0.30, "white")
        return border_all(1.5 if accent else 1, col)

    def glass(content, padding=20, corner=24, margin=None):
        box = ft.Container(content=content, padding=padding, margin=margin,
                           border_radius=radius(corner), bgcolor=panel_color(),
                           blur=glass_blur())
        glass_panels.append(box)
        return box

    def make_btn(content, on_click, size=52, accent=False):
        c = ft.Container(content=content, width=size, height=size, alignment=CENTER,
                         bgcolor=btn_fill(accent), border=btn_edge(accent),
                         border_radius=radius(p["button_radius"]),
                         on_click=on_click, ink=True, animate=smooth(150))
        ghost_buttons.append((c, accent))
        return c

    def ghost_btn(label, on_click, icon_name=None, accent=False, register_btn=False,
                  disabled=False, label_ref=None):
        """Прозрачная кнопка с подписью."""
        txt = label_ref or ft.Text(label, size=13, color="white")
        row = ([make_icon(icon_name, size=18, color="white")] if icon_name else []) + [txt]
        c = ft.Container(
            ft.Row(row, tight=True, spacing=6, alignment=ft.MainAxisAlignment.CENTER),
            padding=ft.Padding(14, 10, 14, 10), bgcolor=btn_fill(accent),
            border=btn_edge(accent), border_radius=radius(p["button_radius"]),
            on_click=None if disabled else on_click, ink=not disabled,
            opacity=0.4 if disabled else 1, animate=smooth(150))
        if register_btn:
            ghost_buttons.append((c, accent))
        return c

    def restyle_buttons():
        for c, acc in ghost_buttons:
            c.bgcolor = btn_fill(acc)
            c.border = btn_edge(acc)
            c.border_radius = radius(p["button_radius"])

    def apply_accent():
        a = p["accent_now"]
        for s in accent_sliders:
            s.active_color = a
        nav.indicator_color = ft.Colors.with_opacity(0.45, a)
        art_holder.gradient = ft.LinearGradient(
            begin=ft.Alignment(-1, -1), end=ft.Alignment(1, 1),
            colors=[a, logic.darken(a)])
        apply_viz_colors()
        render_shuffle()
        render_repeat()
        restyle_buttons()
        render_viz_chips()

    async def refresh_accent(track):
        color = None
        if p["dynamic_accent"] and track is not None and track.thumb:
            rgb = await asyncio.to_thread(dominant_color, track.thumb)
            if rgb:
                color = logic.accent_from_rgb(*rgb)
        new = color or p["accent"]
        if current_track() is track and new != p["accent_now"]:
            p["accent_now"] = new
            apply_accent()
            page.update()

    # ======================= Аудио =======================
    track_title = ft.Text(NO_TRACK, size=22, weight=ft.FontWeight.BOLD, color="white",
                          max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    artist_name = ft.Text(NO_ARTIST, size=14, color="white70", max_lines=1,
                          overflow=ft.TextOverflow.ELLIPSIS)
    queue_label = ft.Text("", size=12, color="white54", max_lines=1,
                          overflow=ft.TextOverflow.ELLIPSIS)
    time_now = ft.Text("0:00", size=12, color="white70")
    time_total = ft.Text("--:--", size=12, color="white70")

    def update_time_labels():
        """Слева - сколько прошло, справа - вся длина (или сколько осталось)."""
        dur = effective_duration()
        time_now.value = fmt_time(p["position_ms"])
        if not dur:
            time_total.value = "--:--"
        elif p["show_remaining"]:
            time_total.value = logic.format_remaining_label(p["position_ms"], dur)
        else:
            time_total.value = fmt_time(dur)

    def toggle_remaining(e=None):
        p["show_remaining"] = not p["show_remaining"]
        update_time_labels()
        persist()
        page.update()

    def reset_anchor():
        p["anchor"] = (p["position_ms"], time.monotonic())
    progress_slider = ft.Slider(min=0, max=100, value=saved["position_percent"],
                                active_color=p["accent"], inactive_color="white24")
    accent_sliders.append(progress_slider)

    def effective_duration() -> int:
        """Длительность в мс: от плеера или, если её нет, из данных трека."""
        if p["duration_ms"]:
            return p["duration_ms"]
        t = current_track()
        return int(t.duration * 1000) if t and t.duration else 0

    async def on_duration_change(e):
        p["duration_ms"] = to_ms(e.duration)
        t = current_track()
        if t and p["duration_ms"] and not t.duration:
            t.duration = p["duration_ms"] // 1000      # запоминаем длину трека
        update_time_labels()
        page.update()

    async def on_position_change(e):
        p["position_ms"] = to_ms(e.position)
        reset_anchor()
        update_time_labels()
        dur = effective_duration()
        if dur and not p["dragging"]:
            progress_slider.value = min(100.0, p["position_ms"] / dur * 100)
        p["tick"] += 1
        if p["tick"] % 5 == 0:
            persist()
        page.update()

    async def on_state_change(e):
        if "completed" in str(e.state).lower():
            await on_track_finished()

    audio_kwargs = dict(autoplay=False, on_duration_change=on_duration_change,
                        on_position_change=on_position_change,
                        on_state_change=on_state_change)
    try:
        audio = fta.Audio(volume=p["volume"], playback_rate=p["speed"], **audio_kwargs)
    except TypeError:
        audio = fta.Audio(**audio_kwargs)
    picker = ft.FilePicker()
    add_service(page, audio)
    add_service(page, picker)

    async def audio_set(name: str, value):
        """Громкость и скорость: методом set_* (новые версии) или свойством."""
        try:
            setter = getattr(audio, f"set_{name}", None)
            if setter is not None:
                res = setter(value)
                if asyncio.iscoroutine(res):
                    await res
            else:
                setattr(audio, name, value)
                audio.update()
        except Exception as err:
            print(f">>> Не удалось изменить {name}: {err}")

    # ======================= Иконки и обложка =======================
    play_pause_icon = make_icon("play_arrow", size=34, color="white")
    fav_icon = make_icon("favorite_border", size=26, color="white")
    shuffle_icon = make_icon("shuffle", size=22, color="white54")
    repeat_icon = make_icon("repeat", size=22, color="white54")
    mini_play_icon = make_icon("play_arrow", size=26, color="white")
    art_icon = make_icon("music_note", size=96, color="white70")
    art_switcher = make_switcher(art_icon, 350)
    art_holder = ft.Container(
        content=art_switcher, width=p["art"], height=p["art"], alignment=CENTER,
        border_radius=radius(36), clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        gradient=ft.LinearGradient(begin=ft.Alignment(-1, -1), end=ft.Alignment(1, 1),
                                   colors=[p["accent"], logic.darken(p["accent"])]))
    mini_title = ft.Text(NO_TRACK, size=14, weight=ft.FontWeight.W_600, color="white",
                         max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
    mini_artist = ft.Text(NO_ARTIST, size=12, color="white70", max_lines=1,
                          overflow=ft.TextOverflow.ELLIPSIS)

    def set_art(track):
        if track is not None and track.thumb:
            art_switcher.content = ft.Image(src=track.thumb, width=p["art"], height=p["art"],
                                            fit=COVER)
        else:
            art_switcher.content = art_icon

    def render_play():
        name = "pause" if p["is_playing"] else "play_arrow"
        set_icon(play_pause_icon, name)
        set_icon(mini_play_icon, name)

    def render_favorite():
        t = current_track()
        fav = bool(t and t.id in p["favorites"])
        set_icon(fav_icon, "favorite" if fav else "favorite_border")
        fav_icon.color = "#ff5d7a" if fav else "white"

    def render_shuffle():
        shuffle_icon.color = p["accent_now"] if p["shuffle"] else "white54"

    def render_repeat():
        set_icon(repeat_icon, "repeat_one" if p["repeat_mode"] == 2 else "repeat")
        repeat_icon.color = "white54" if p["repeat_mode"] == 0 else p["accent_now"]

    def render_mini():
        mini_bar.visible = p["view"] != 0 and current_track() is not None

    def show_track_info():
        t = current_track()
        track_title.value = mini_title.value = t.title if t else NO_TRACK
        artist_name.value = mini_artist.value = t.artist if t else NO_ARTIST
        if t:
            rest = sum(x.duration for x in p["queue"][p["index"] + 1:])
            tail = f" · дальше ~{max(1, round(rest / 60))} мин" if rest else ""
            queue_label.value = (f"{p['queue_name'] or 'Очередь'} · "
                                 f"{p['index'] + 1} из {len(p['queue'])}{tail}")
        else:
            queue_label.value = ""
        set_art(t)
        render_favorite()
        render_next()
        update_time_labels()
        render_mini()

    # ======================= Воспроизведение =======================
    async def start_playback():
        if not p["started"]:                      # новый запуск трека -> в историю
            t = current_track()
            if t:
                logic.record_play(p["stats"], t.id)
        start = p["resume_ms"] or 0
        if start:
            await audio.play(make_duration(start))
        else:
            await audio.play()
        p["started"] = True
        p["position_ms"] = start
        reset_anchor()
        p["resume_ms"] = 0
        p["is_playing"] = True
        render_play()
        page.update()

    async def load_track(index: int, play: bool = True, start_ms: int = 0):
        if not p["queue"]:
            return
        p["index"] = index % len(p["queue"])
        track = current_track()
        if track.source == "local" and not track.duration:
            track.duration = read_duration(track.path)     # длина локального файла
        p["load_id"] += 1
        my_id = p["load_id"]
        p["need_load"] = False
        if p["started"]:
            try:
                await audio.pause()
            except Exception:
                pass
        p["is_playing"] = False
        p["started"] = False
        p["duration_ms"] = 0
        p["position_ms"] = start_ms
        p["resume_ms"] = start_ms
        if not start_ms:
            progress_slider.value = 0
        update_time_labels()
        show_track_info()
        render_play()
        if track.source != "local":
            where = "Telegram" if track.source == "telegram" else "SoundCloud"
            artist_name.value = mini_artist.value = f"Загрузка из {where}…"
        refresh_library()
        page.update()
        spawn(refresh_accent, track)
        try:
            path = await repo.resolve(track)
        except Exception as err:
            if my_id == p["load_id"]:
                show_track_info()
                page.update()
                toast(page, f"Не удалось загрузить трек: {err}")
            return
        if my_id != p["load_id"]:
            return
        audio.src = path
        show_track_info()
        page.update()
        if play:
            await asyncio.sleep(0.1)
            await start_playback()
        persist()

    async def play_queue(tracks, index=0, name=""):
        if not tracks:
            return
        register(tracks)
        p["queue"], p["queue_name"] = list(tracks), name
        await load_track(index, play=True)

    def ensure_in_queue(track) -> int:
        register([track])
        for i, t in enumerate(p["queue"]):
            if t.id == track.id:
                return i
        p["queue"].append(track)
        p["queue_name"] = p["queue_name"] or "Очередь"
        return len(p["queue"]) - 1

    def queue_play_next(track):
        register([track])
        cur = current_track()
        if cur is None:
            p["queue"], p["queue_name"], p["index"] = [track], "Очередь", 0
            p["need_load"] = True
            show_track_info()
            return
        if cur.id == track.id:
            return
        for i, t in enumerate(p["queue"]):
            if t.id == track.id:
                p["queue"].pop(i)
                if i < p["index"]:
                    p["index"] -= 1
                break
        p["queue"].insert(p["index"] + 1, track)
        show_track_info()

    def move_in_queue(i: int, j: int):
        if not (0 <= i < len(p["queue"]) and 0 <= j < len(p["queue"])):
            return
        p["queue"].insert(j, p["queue"].pop(i))
        if p["index"] == i:
            p["index"] = j
        elif i < p["index"] <= j:
            p["index"] -= 1
        elif j <= p["index"] < i:
            p["index"] += 1
        show_track_info()

    async def pick_local(e):
        files = await picker.pick_files(
            dialog_title="Выбери музыку", allow_multiple=True,
            file_type=ft.FilePickerFileType.AUDIO)
        paths = [f.path for f in (files or []) if f.path]
        if not paths:
            toast(page, "Файлы не выбраны или устройство не отдало путь к файлу")
            return
        if IS_MOBILE:      # на телефоне путь из выбора временный: сохраняем копию у себя
            toast(page, "Копирую файлы в память приложения…")
            page.update()
            paths = await asyncio.to_thread(copy_into_music, paths)
            if not paths:
                toast(page, "Не удалось скопировать файлы")
                return
        new = repo.local.tracks_from_paths(paths)
        register(new)
        if not p["queue"] or not p["is_playing"]:
            await play_queue(new, 0, "Выбранные файлы")
        else:
            for t in new:
                ensure_in_queue(t)
            toast(page, f"Добавлено в очередь: {len(new)}")
        refresh_library()
        persist()
        page.update()

    async def toggle_play(e=None):
        if not p["queue"]:
            await pick_local(e)
            return
        if p["need_load"]:
            await load_track(p["index"], play=True, start_ms=p["position_ms"])
            return
        if p["is_playing"]:
            await audio.pause()
            p["is_playing"] = False
        elif p["started"]:
            await audio.resume()
            reset_anchor()
            p["is_playing"] = True
        else:
            await start_playback()
        render_play()
        persist()
        page.update()

    async def pause_playback():
        if p["is_playing"]:
            await audio.pause()
            p["is_playing"] = False
            render_play()
            persist()

    def peek_next(ignore_repeat_one=False):
        """Индекс трека, который сыграет следующим (или None, если очередь закончится)."""
        chosen = next((i for i, t in enumerate(p["queue"]) if t.id == p["shuffle_next_id"]),
                      None)
        i = logic.next_queue_index(p["index"], len(p["queue"]), p["shuffle"],
                                   p["repeat_mode"], chosen, random, ignore_repeat_one)
        if p["shuffle"] and i is not None and i != p["index"]:
            p["shuffle_next_id"] = p["queue"][i].id     # показанный и сыгранный совпадут
        return i

    async def next_track(e=None):
        if p["queue"]:
            i = peek_next(ignore_repeat_one=True)
            await load_track(0 if i is None else i, play=True)

    async def prev_track(e=None):
        if not p["queue"]:
            return
        if p["position_ms"] > 3000 and p["started"]:
            await audio.seek(make_duration(0))
            p["position_ms"] = 0
            reset_anchor()
            update_time_labels()
            page.update()
        else:
            await load_track(p["index"] - 1, play=True)

    async def seek_by(delta_ms: int):
        dur = effective_duration()
        if p["started"] and dur:
            target = logic.seek_target(p["position_ms"], delta_ms, dur)
            await audio.seek(make_duration(target))
            p["position_ms"] = target
            reset_anchor()
            update_time_labels()
            page.update()

    async def on_track_finished():
        if not p["queue"]:
            return
        if p["sleep_eot"]:                         # таймер сна «до конца трека»
            p["sleep_eot"] = False
            sleep_label.value = ""
            p["is_playing"] = False
            render_play()
            page.update()
            return
        mode = p["repeat_mode"]
        if mode == 2:
            await load_track(p["index"], play=True)
        elif mode == 1 or p["shuffle"] or p["index"] < len(p["queue"]) - 1:
            await next_track()
        else:
            p["is_playing"] = False
            render_play()
            page.update()

    async def stop_and_reset():
        p["load_id"] += 1
        try:
            if p["started"]:
                await audio.pause()
        except Exception:
            pass
        p["is_playing"] = p["started"] = False
        p["position_ms"] = p["duration_ms"] = 0
        progress_slider.value = 0
        update_time_labels()

    async def remove_from_queue(i: int):
        if not (0 <= i < len(p["queue"])):
            return
        was_current = i == p["index"]
        p["queue"].pop(i)
        if was_current:
            await stop_and_reset()
            p["index"] = min(i, max(0, len(p["queue"]) - 1))
            p["need_load"] = bool(p["queue"])
        elif i < p["index"]:
            p["index"] -= 1
        show_track_info()
        render_play()

    async def delete_from_library(tid: str):
        p["tracks"].pop(tid, None)
        p["favorites"].discard(tid)
        for pl in p["playlists"]:
            if tid in pl["tracks"]:
                pl["tracks"].remove(tid)
        for i, t in enumerate(p["queue"]):
            if t.id == tid:
                await remove_from_queue(i)
                break
        refresh_library()
        persist()
        page.update()

    # ======================= Слайдеры, громкость, скорость =======================
    async def commit_seek():
        """Применяет положение ползунка: перематывает трек."""
        p["seek_token"] += 1
        p["dragging"] = False
        dur = effective_duration()
        if dur:
            target = int(progress_slider.value / 100 * dur)
            p["position_ms"] = target
            reset_anchor()
            update_time_labels()
            if p["started"]:
                await audio.seek(make_duration(target))
            else:
                p["resume_ms"] = target      # трек ещё не запущен: начнём с этого места
        persist()
        page.update()

    async def drag_watchdog(token):
        """Если событие «отпустили» не пришло, не даём ползунку застрять."""
        await asyncio.sleep(3)
        if p["seek_token"] == token and p["dragging"]:
            p["dragging"] = False

    async def on_slider_start(e):
        p["dragging"] = True
        p["seek_token"] += 1
        spawn(drag_watchdog, p["seek_token"])

    async def on_slider_change(e):
        p["dragging"] = True
        p["seek_token"] += 1
        token = p["seek_token"]
        dur = effective_duration()
        if dur:                               # время обновляется прямо во время движения
            time_now.value = fmt_time(int(progress_slider.value / 100 * dur))
            page.update()
        await asyncio.sleep(0.6)              # запасной вариант, если «отпустили» не пришло
        if p["seek_token"] == token:
            await commit_seek()

    async def on_slider_end(e):
        await commit_seek()

    progress_slider.on_change_start = on_slider_start
    progress_slider.on_change = on_slider_change
    progress_slider.on_change_end = on_slider_end

    async def progress_loop():
        """Двигает ползунок плавно (5 раз в секунду) между событиями плеера."""
        while True:
            await asyncio.sleep(0.2)
            if not (p["is_playing"] and p["started"]) or p["dragging"] or not p["anchor"]:
                continue
            dur = effective_duration()
            base, t0 = p["anchor"]
            pos = base + int((time.monotonic() - t0) * 1000 * p["speed"])
            if dur:
                pos = min(pos, dur)
                progress_slider.value = min(100.0, pos / dur * 100)
            p["position_ms"] = pos
            update_time_labels()
            page.update()

    async def on_volume(e):
        p["volume"] = float(e.control.value) / 100
        await audio_set("volume", p["volume"])
        persist()

    volume_slider = ft.Slider(min=0, max=100, value=round(p["volume"] * 100),
                              active_color=p["accent"], inactive_color="white24",
                              on_change=on_volume, expand=True)
    accent_sliders.append(volume_slider)
    speed_text = ft.Text(f"{p['speed']:g}×", size=13, color="white")

    async def cycle_speed(e):
        speeds = logic.SPEEDS
        cur = min(range(len(speeds)), key=lambda i: abs(speeds[i] - p["speed"]))
        p["speed"] = speeds[(cur + 1) % len(speeds)]
        speed_text.value = f"{p['speed']:g}×"
        await audio_set("playback_rate", p["speed"])
        persist()
        page.update()

    async def nudge_volume(delta: float):
        p["volume"] = max(0.0, min(1.0, p["volume"] + delta))
        volume_slider.value = round(p["volume"] * 100)
        await audio_set("volume", p["volume"])
        persist()
        page.update()

    # ======================= Кнопки плеера =======================
    def toggle_favorite(e=None):
        t = current_track()
        if not t:
            return
        p["favorites"].symmetric_difference_update({t.id})
        render_favorite()
        refresh_library()
        persist()
        page.update()

    def toggle_shuffle(e=None):
        p["shuffle"] = not p["shuffle"]
        p["shuffle_next_id"] = None
        render_shuffle()
        render_next()
        persist()
        page.update()

    def toggle_repeat(e=None):
        p["repeat_mode"] = (p["repeat_mode"] + 1) % 3
        render_repeat()
        render_next()
        persist()
        page.update()

    play_btn = make_btn(play_pause_icon, toggle_play, size=76, accent=True)
    shuffle_btn = make_btn(shuffle_icon, toggle_shuffle, size=46)
    repeat_btn = make_btn(repeat_icon, toggle_repeat, size=46)
    prev_btn = make_btn(make_icon("skip_previous", size=28, color="white"), prev_track)
    next_btn = make_btn(make_icon("skip_next", size=28, color="white"), next_track)
    fav_btn = ft.Container(content=fav_icon, width=44, height=44, alignment=CENTER,
                           border_radius=radius(22), on_click=toggle_favorite, ink=True)

    # ======================= Диалоги =======================
    def ask_name(title, initial, on_ok, ok_label="Сохранить"):
        field = ft.TextField(value=initial, hint_text="Название плейлиста", autofocus=True)

        def ok(ev):
            name = (field.value or "").strip()
            if not name:
                return
            close_dialog(page, dlg)
            on_ok(name)

        def cancel(ev):
            close_dialog(page, dlg)

        field.on_submit = ok
        dlg = ft.AlertDialog(title=ft.Text(title), content=field,
                             actions=[ft.TextButton(ok_label, on_click=ok),
                                      ft.TextButton("Отмена", on_click=cancel)])
        open_dialog(page, dlg)

    def add_to_playlist_dialog(track: Track):
        def choose(pid):
            def handler(ev):
                close_dialog(page, dlg)
                added = add_to_playlist(pid, track)
                toast(page, f"Добавлено в «{playlist_name(pid)}»" if added
                      else "Этот трек уже есть в плейлисте")
                refresh_library()
                persist()
                page.update()
            return handler

        def new_playlist(ev):
            close_dialog(page, dlg)

            def created(name):
                pl = create_playlist(name)
                add_to_playlist(pl["id"], track)
                toast(page, f"Создан плейлист «{pl['name']}»")
                refresh_library()
                persist()
                page.update()
            ask_name("Новый плейлист", "", created, "Создать")

        def cancel(ev):
            close_dialog(page, dlg)

        items = [ft.ListTile(
            leading=make_icon("queue_music", size=22),
            title=ft.Text(pl["name"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            subtitle=ft.Text(f"Треков: {len(pl['tracks'])}"),
            on_click=choose(pl["id"])) for pl in p["playlists"]]
        items.append(ft.ListTile(leading=make_icon("add", size=22),
                                 title=ft.Text("Новый плейлист…"), on_click=new_playlist))
        dlg = ft.AlertDialog(
            title=ft.Text("Добавить в плейлист"),
            content=ft.Container(ft.Column(items, tight=True, scroll=ft.ScrollMode.AUTO),
                                 width=320),
            actions=[ft.TextButton("Отмена", on_click=cancel)])
        open_dialog(page, dlg)

    # ----- текст песни -----
    def open_lyrics(track: Track):
        guess_artist, guess_title = logic.guess_artist_title(track.title, track.artist)
        if guess_artist in ("", NO_ARTIST, "Telegram"):
            guess_artist = ""
        artist_f = ft.TextField(label="Исполнитель", value=guess_artist)
        title_f = ft.TextField(label="Название", value=guess_title)
        out = ft.Text("Проверь исполнителя и название, затем нажми «Найти».",
                      size=13, selectable=True)
        busy = ft.ProgressBar(visible=False)

        async def search(ev):
            busy.visible = True
            out.value = "Ищу текст…"
            page.update()
            try:
                out.value = await asyncio.to_thread(
                    fetch_lyrics, artist_f.value or "", title_f.value or "")
            except Exception as err:
                out.value = f"{err}. Попробуй изменить исполнителя или название."
            busy.visible = False
            page.update()

        def close(ev):
            close_dialog(page, dlg)

        dlg = ft.AlertDialog(
            title=ft.Text("Текст песни"),
            content=ft.Container(ft.Column(
                [artist_f, title_f, busy, out], tight=True, scroll=ft.ScrollMode.AUTO),
                width=340, height=420),
            actions=[ft.TextButton("Найти", on_click=search),
                     ft.TextButton("Закрыть", on_click=close)])
        open_dialog(page, dlg)

    # ----- радио по треку -----
    async def start_radio(track: Track):
        if not repo.soundcloud.available:
            toast(page, "Для радио нужен yt-dlp: pip install yt-dlp")
            return
        toast(page, "Ищу похожие треки…")
        try:
            similar = await repo.soundcloud.related(track, 20)
        except Exception as err:
            toast(page, f"Не удалось найти похожие: {err}")
            return
        similar = [t for t in similar if t.id != track.id]
        if not similar:
            toast(page, "Похожих треков не нашлось")
            return
        await play_queue([track] + similar, 0, f"Радио · {track.title}"[:50])
        refresh_library()
        persist()

    # ----- меню действий с треком -----
    def open_track_actions(t: Track, pid=None):
        def run(fn, *args, is_async=False):
            def handler(ev):
                close_dialog(page, dlg)
                if is_async:
                    spawn(fn, *args)
                else:
                    fn(*args)
            return handler

        async def play_now(track):
            idx = ensure_in_queue(track)
            await load_track(idx, play=True)
            refresh_library()
            persist()
            page.update()

        def play_next_do(track):
            queue_play_next(track)
            toast(page, "Сыграет следующим")
            persist()
            page.update()

        def append_do(track):
            ensure_in_queue(track)
            show_track_info()
            toast(page, "Добавлено в очередь")
            persist()
            page.update()

        async def remove_do(track):
            if pid == ALL:
                await delete_from_library(track.id)
                toast(page, "Трек убран из приложения (файл на диске не тронут)")
            elif pid == FAV:
                p["favorites"].discard(track.id)
                render_favorite()
            else:
                pl = get_playlist(pid)
                if pl and track.id in pl["tracks"]:
                    pl["tracks"].remove(track.id)
            refresh_library()
            persist()
            page.update()

        entries = [
            ("play_arrow", "Играть", run(play_now, t, is_async=True)),
            ("queue_play_next", "Играть следующим", run(play_next_do, t)),
            ("playlist_play", "Добавить в очередь", run(append_do, t)),
            ("playlist_add", "Добавить в плейлист…", run(add_to_playlist_dialog, t)),
            ("radio", "Радио по треку", run(start_radio, t, is_async=True)),
            ("lyrics", "Текст песни", run(open_lyrics, t)),
        ]
        if pid is not None and pid not in (RECENT, TOP):
            label = {ALL: "Удалить из приложения", FAV: "Убрать из избранного"}.get(
                pid, "Убрать из плейлиста")
            entries.append(("delete", label, run(remove_do, t, is_async=True)))

        def cancel(ev):
            close_dialog(page, dlg)

        dlg = ft.AlertDialog(
            title=ft.Text(t.title, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
            content=ft.Container(ft.Column(
                [ft.ListTile(leading=make_icon(i, size=22), title=ft.Text(txt), on_click=h)
                 for i, txt, h in entries], tight=True, scroll=ft.ScrollMode.AUTO),
                width=320),
            actions=[ft.TextButton("Закрыть", on_click=cancel)])
        open_dialog(page, dlg)

    # ----- очередь -----
    queue_list = ft.ListView(spacing=4, expand=True)

    def render_queue():
        rows = []
        for i, t in enumerate(p["queue"]):
            def play_it(ev, i=i):
                spawn(play_index, i)

            def up(ev, i=i):
                move_in_queue(i, i - 1)
                render_queue()
                page.update()

            def down(ev, i=i):
                move_in_queue(i, i + 1)
                render_queue()
                page.update()

            def drop(ev, i=i):
                spawn(drop_index, i)

            cur = i == p["index"]
            rows.append(ft.Container(
                ft.Row([
                    ft.Column([
                        ft.Text(f"{'▶ ' if cur else ''}{t.title}", size=13, max_lines=1,
                                weight=ft.FontWeight.BOLD if cur else None,
                                overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(t.artist, size=11, max_lines=1, opacity=0.7,
                                overflow=ft.TextOverflow.ELLIPSIS)],
                        expand=True, spacing=0),
                    ft.IconButton(icon=icon_value("arrow_upward"), icon_size=18, on_click=up),
                    ft.IconButton(icon=icon_value("arrow_downward"), icon_size=18, on_click=down),
                    ft.IconButton(icon=icon_value("close"), icon_size=18, on_click=drop),
                ], spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding(10, 2, 0, 2), border_radius=radius(12), on_click=play_it,
                bgcolor=ft.Colors.with_opacity(0.25, p["accent_now"]) if cur else None))
        queue_list.controls = rows or [ft.Text("Очередь пуста")]

    async def play_index(i):
        await load_track(i, play=True)
        render_queue()
        page.update()

    async def drop_index(i):
        await remove_from_queue(i)
        render_queue()
        persist()
        page.update()

    def open_queue(e=None):
        render_queue()

        async def clear(ev):
            await stop_and_reset()
            p["queue"], p["index"], p["need_load"] = [], 0, False
            show_track_info()
            render_play()
            render_queue()
            persist()
            page.update()

        def save_queue(ev):
            if not p["queue"]:
                return
            close_dialog(page, dlg)

            def created(name):
                pl = create_playlist(name)
                register(p["queue"])
                pl["tracks"] = [t.id for t in p["queue"]]
                toast(page, f"Очередь сохранена: «{name}»")
                refresh_library()
                persist()
                page.update()
            ask_name("Сохранить очередь как плейлист", p["queue_name"] or "", created, "Создать")

        def close(ev):
            close_dialog(page, dlg)

        dlg = ft.AlertDialog(
            title=ft.Text(f"Очередь ({len(p['queue'])})"),
            content=ft.Container(queue_list, width=340, height=420),
            actions=[ft.TextButton("Очистить", on_click=clear),
                     ft.TextButton("В плейлист", on_click=save_queue),
                     ft.TextButton("Закрыть", on_click=close)])
        open_dialog(page, dlg)

    # ----- таймер сна -----
    sleep_label = ft.Text("", size=12, color="white70")

    async def sleep_loop(token):
        while p["sleep_token"] == token and p["sleep_until"]:
            left = p["sleep_until"] - time.time()
            if left <= 0:
                p["sleep_until"] = None
                sleep_label.value = ""
                await pause_playback()
                toast(page, "Таймер сна: музыка выключена")
                page.update()
                return
            sleep_label.value = f"Сон через {logic.fmt_remaining(left)}"
            page.update()
            await asyncio.sleep(1)

    def set_sleep(minutes=None, end_of_track=False):
        p["sleep_token"] += 1
        p["sleep_until"], p["sleep_eot"] = None, False
        sleep_label.value = ""
        if minutes:
            p["sleep_until"] = time.time() + minutes * 60
            spawn(sleep_loop, p["sleep_token"])
        elif end_of_track:
            p["sleep_eot"] = True
            sleep_label.value = "Сон: в конце трека"
        page.update()

    def open_sleep(e=None):
        def choose(**kw):
            def handler(ev):
                close_dialog(page, dlg)
                set_sleep(**kw)
            return handler

        items = [ft.ListTile(leading=make_icon("bedtime", size=22),
                             title=ft.Text(f"Через {m} мин"), on_click=choose(minutes=m))
                 for m in SLEEP_OPTIONS]
        items.append(ft.ListTile(leading=make_icon("music_off", size=22),
                                 title=ft.Text("В конце трека"),
                                 on_click=choose(end_of_track=True)))
        items.append(ft.ListTile(leading=make_icon("alarm_off", size=22),
                                 title=ft.Text("Выключить таймер"), on_click=choose()))
        dlg = ft.AlertDialog(
            title=ft.Text("Таймер сна"),
            content=ft.Container(ft.Column(items, tight=True, scroll=ft.ScrollMode.AUTO),
                                 width=300),
            actions=[ft.TextButton("Закрыть", on_click=lambda ev: close_dialog(page, dlg))])
        open_dialog(page, dlg)

    # ======================= Экран «Плеер» =======================
    viz_switch = ft.Switch(value=p["visualizer"], on_change=lambda e: set_visualizer(
        bool(e.control.value)))
    viz_items = []
    viz_row = ft.Row([], height=44, visible=p["visualizer"], alignment=ft.MainAxisAlignment.CENTER)
    viz_chips_row = ft.Row([], wrap=True, spacing=8, run_spacing=8)

    def build_viz():
        """Собирает элементы эквалайзера под выбранный стиль."""
        style = p["viz_style"]
        spec = logic.VIZ_SPEC[style]
        items = []
        for _ in range(spec["n"]):
            if style == "dots":
                dot = ft.Container(width=spec["w"], height=spec["w"], bgcolor=bar_color(),
                                   border_radius=radius(spec["w"] / 2))
                items.append(ft.Container(dot, width=spec["w"], height=44,
                                          alignment=ft.Alignment(0, 1.0), animate=smooth(160)))
            else:
                items.append(ft.Container(width=spec["w"], height=5, bgcolor=bar_color(),
                                          border_radius=radius(spec["radius"]),
                                          animate=smooth(160)))
        viz_items[:] = items
        viz_row.controls = items
        viz_row.spacing = spec["gap"]
        viz_row.vertical_alignment = (ft.CrossAxisAlignment.END if spec["align"] == "end"
                                      else ft.CrossAxisAlignment.CENTER)

    def apply_viz_colors():
        for it in viz_items:
            if p["viz_style"] == "dots":
                it.content.bgcolor = bar_color()
            else:
                it.bgcolor = bar_color()

    def viz_set(values):
        for it, v in zip(viz_items, values):
            if p["viz_style"] == "dots":
                it.alignment = ft.Alignment(0, v)
            else:
                it.height = v

    async def viz_loop():
        """Декоративный эквалайзер: это анимация, а не реальный спектр звука."""
        p["viz_token"] += 1
        token = p["viz_token"]
        t0 = time.monotonic()
        while p["viz_token"] == token and p["visualizer"]:
            if p["view"] != 0:
                await asyncio.sleep(0.5)
            elif p["is_playing"]:
                viz_set(logic.viz_values(p["viz_style"], time.monotonic() - t0, random))
                page.update()
                await asyncio.sleep(0.18)
            else:
                viz_set([logic.viz_idle_value(p["viz_style"])] * len(viz_items))
                page.update()
                await asyncio.sleep(0.5)

    def set_visualizer(enabled: bool):
        p["visualizer"] = enabled
        viz_row.visible = enabled
        viz_switch.value = enabled
        if enabled:
            spawn(viz_loop)
        else:
            p["viz_token"] += 1
        persist()
        page.update()

    def render_viz_chips():
        viz_chips_row.controls = [
            ghost_btn(label, (lambda e, k=key: set_viz_style(k)), accent=(key == p["viz_style"]))
            for key, label in logic.VIZ_STYLES]

    def set_viz_style(style: str):
        p["viz_style"] = style
        build_viz()
        render_viz_chips()
        if not p["visualizer"]:
            set_visualizer(True)
            return
        persist()
        page.update()

    def cycle_viz_style(e=None):
        set_viz_style(logic.next_viz_style(p["viz_style"]))
        toast(page, f"Стиль эквалайзера: {dict(logic.VIZ_STYLES)[p['viz_style']]}")

    build_viz()
    render_viz_chips()

    # ----- блок «Далее» -----
    next_label = ft.Text("Далее", size=11, color="white54")
    next_title = ft.Text("", size=14, weight=ft.FontWeight.W_600, color="white", max_lines=1,
                         overflow=ft.TextOverflow.ELLIPSIS)
    next_artist = ft.Text("", size=12, color="white70", max_lines=1,
                          overflow=ft.TextOverflow.ELLIPSIS)
    next_thumb = ft.Container(width=44, height=44, alignment=CENTER)

    def render_next():
        t = current_track()
        if t is None:
            next_label.value, next_title.value, next_artist.value = "Далее", "Очередь пуста", ""
            next_thumb.content = None
            return
        i = peek_next()
        if i is None:
            next_label.value, next_title.value, next_artist.value = (
                "Конец очереди", "Дальше ничего не играет", "")
            next_thumb.content = make_icon("flag", size=22, color="white54")
            return
        nt = p["queue"][i]
        next_label.value = ("Повтор этого трека" if i == p["index"]
                            else "Далее · случайный" if p["shuffle"] else "Далее")
        next_title.value, next_artist.value = nt.title, nt.artist
        next_thumb.content = thumb_box(nt, 44)

    async def play_next_card(e):
        i = peek_next(ignore_repeat_one=True)
        if i is not None:
            await load_track(i, play=True)

    next_card = glass(ft.Row([
        next_thumb,
        ft.Column([next_label, next_title, next_artist], expand=True, spacing=0),
        make_icon("skip_next", size=22, color="white54"),
    ], vertical_alignment=ft.CrossAxisAlignment.CENTER), padding=10, corner=20)
    next_card.on_click = play_next_card
    next_card.ink = True

    player_card = glass(ft.Column([
        ft.Row([ft.Column([track_title, artist_name], expand=True, spacing=2), fav_btn],
               alignment=SPACE_BETWEEN),
        progress_slider,
        ft.Row([time_now, ft.Container(time_total, on_click=toggle_remaining,
                                       tooltip="Нажми: вся длина / осталось",
                                       padding=ft.Padding(8, 2, 0, 2))],
               alignment=SPACE_BETWEEN),
        ft.Container(height=4),
        ft.Row([shuffle_btn, prev_btn, play_btn, next_btn, repeat_btn],
               alignment=SPACE_AROUND, vertical_alignment=ft.CrossAxisAlignment.CENTER),
    ], tight=True, spacing=6))

    extras_card = glass(ft.Column([
        ft.Row([make_icon("volume_up", size=20, color="white70"), volume_slider]),
        ft.Row([
            ghost_btn("Очередь", open_queue, "queue_music", register_btn=True),
            ghost_btn("", cycle_speed, "speed", register_btn=True, label_ref=speed_text),
            ghost_btn("Сон", open_sleep, "bedtime", register_btn=True),
            ghost_btn("Эквалайзер", lambda e: set_visualizer(not p["visualizer"]),
                      "equalizer", register_btn=True),
            ghost_btn("Стиль", cycle_viz_style, "style", register_btn=True),
            ghost_btn("Текст", lambda e: current_track() and open_lyrics(current_track()),
                      "lyrics", register_btn=True),
        ], wrap=True, spacing=8, run_spacing=8, alignment=ft.MainAxisAlignment.CENTER),
        ft.Container(sleep_label, alignment=CENTER),
    ], tight=True, spacing=8), padding=14)

    player_view = ft.Column(
        [ft.Container(art_holder, alignment=CENTER, padding=ft.Padding(0, 8, 0, 4)),
         viz_row, ft.Container(queue_label, alignment=CENTER), player_card, next_card,
         extras_card],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        scroll=ft.ScrollMode.AUTO, expand=True, spacing=10)

    # ======================= Общие элементы списков =======================
    def thumb_box(track, size=52):
        inner = (ft.Image(src=track.thumb, width=size, height=size, fit=COVER)
                 if track.thumb else make_icon(SOURCE_ICON.get(track.source, "music_note"),
                                               size=24, color="white70"))
        return ft.Container(inner, width=size, height=size, alignment=CENTER,
                            border_radius=radius(14),
                            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                            bgcolor=ft.Colors.with_opacity(0.15, "white"))

    def title_text(text, size=28):
        return ft.Text(text, size=size, weight=ft.FontWeight.BOLD, color="white",
                       max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)

    def field_style(**kw):
        return dict(border_radius=radius(24), border_color="transparent",
                    bgcolor=ft.Colors.with_opacity(0.12, "white"), color="white",
                    content_padding=ft.Padding(16, 10, 16, 10), **kw)

    # ======================= Экран «Поиск» =======================
    search_field = ft.TextField(hint_text="Поиск или ссылка SoundCloud", expand=True,
                                **field_style())
    search_progress = ft.ProgressBar(visible=False, color=p["accent"])
    search_status = ft.Text("Введи название или вставь ссылку на трек/плейлист SoundCloud",
                            size=12, color="white70")
    results_list = ft.ListView(expand=True, spacing=8)
    save_results_btn = ft.Container(visible=False)

    def make_result_row(t: Track):
        async def play_now(ev):
            idx = ensure_in_queue(t)
            await load_track(idx, play=True)
            refresh_library()
            persist()

        def more(ev):
            open_track_actions(t)

        dur = f" · {fmt_time(t.duration * 1000)}" if t.duration else ""
        return ft.Container(
            content=ft.Row([
                thumb_box(t),
                ft.Column([
                    ft.Text(t.title, size=14, weight=ft.FontWeight.W_600, color="white",
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(t.artist + dur, size=12, color="white70", max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS)], expand=True, spacing=2),
                ft.IconButton(icon=icon_value("more_vert"), icon_color="white70",
                              on_click=more),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=8, border_radius=radius(18), on_click=play_now, ink=True,
            bgcolor=card_fill())

    def render_results():
        results_list.controls = [make_result_row(t) for t in p["results"]]
        if len(p["results"]) > 1:
            def save_all(ev):
                def created(name):
                    pl = create_playlist(name)
                    register(p["results"])
                    pl["tracks"] = [t.id for t in p["results"]]
                    toast(page, f"Сохранено в «{name}»: {len(pl['tracks'])} треков")
                    refresh_library()
                    persist()
                    page.update()
                ask_name("Сохранить результаты в плейлист", search_field.value or "", created,
                         "Создать")
            save_results_btn.content = ghost_btn("Сохранить всё в плейлист", save_all,
                                                 "playlist_add")
            save_results_btn.visible = True
        else:
            save_results_btn.visible = False

    async def do_search(e=None):
        query = (search_field.value or "").strip()
        if not query:
            return
        if not repo.soundcloud.available:
            toast(page, "Сначала установи yt-dlp: pip install yt-dlp")
            return
        by_link = logic.is_soundcloud_url(query)
        search_progress.visible = True
        search_status.value = "Читаю ссылку…" if by_link else "Ищу…"
        page.update()
        try:
            tracks = await (repo.soundcloud.from_url(query) if by_link
                            else repo.soundcloud.search(query, 15))
            search_status.value = (f"Найдено треков: {len(tracks)}" if tracks and by_link
                                   else "" if tracks else "Ничего не найдено")
        except Exception as err:
            tracks = []
            search_status.value = f"Ошибка: {err}"
        p["results"] = tracks
        render_results()
        search_progress.visible = False
        page.update()

    search_field.on_submit = do_search
    search_btn = make_btn(make_icon("search", size=24, color="white"), do_search,
                          size=48, accent=True)

    search_view = ft.Column([
        title_text("Поиск"),
        ft.Row([search_field, search_btn], spacing=8),
        search_progress, search_status,
        ft.Row([save_results_btn]), results_list,
    ], expand=True, spacing=10)

    # ======================= Экран «Библиотека» =======================
    lib_top = ft.Container()
    lib_body = ft.Container(expand=True)
    lib_search = ft.TextField(hint_text="Поиск в библиотеке", **field_style())

    def on_lib_query(e):
        p["lib_query"] = e.control.value or ""
        build_library_body()
        page.update()

    lib_search.on_change = on_lib_query
    lib_holder = ft.Column([lib_top, lib_search, lib_body], expand=True, spacing=10)

    def open_playlist(pid):
        def handler(ev):
            p["lib_view"] = pid
            refresh_library()
            page.update()
        return handler

    def back_to_overview(ev=None):
        p["lib_view"] = None
        refresh_library()
        page.update()

    def playlist_cover(pid, size=52):
        thumbs = [t.thumb for t in tracks_of(pid)[:4] if t.thumb]
        if pid in SPECIAL or not thumbs:
            tint = {FAV: "#c2415e", RECENT: "#2f7d9e", TOP: "#b4791f"}.get(
                pid, ft.Colors.with_opacity(0.5, p["accent_now"]))
            return ft.Container(
                make_icon(SPECIAL_ICON.get(pid, "queue_music"), size=26, color="white"),
                width=size, height=size, alignment=CENTER, border_radius=radius(16),
                bgcolor=tint)
        half = size // 2
        cells = [ft.Image(src=u, width=half, height=half, fit=COVER) for u in thumbs[:4]]
        while len(cells) < 4:
            cells.append(ft.Container(width=half, height=half,
                                      bgcolor=ft.Colors.with_opacity(0.2, "white")))
        return ft.Container(
            ft.Column([ft.Row(cells[:2], spacing=0), ft.Row(cells[2:], spacing=0)],
                      spacing=0),
            width=size, height=size, border_radius=radius(16),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS)

    def playlist_card(pid, name, count):
        return ft.Container(
            content=ft.Row([
                playlist_cover(pid),
                ft.Column([ft.Text(name, size=15, weight=ft.FontWeight.W_600, color="white",
                                   max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                           ft.Text(f"Треков: {count}", size=12, color="white70")],
                          expand=True, spacing=2),
                make_icon("chevron_right", size=22, color="white54"),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=10, border_radius=radius(20), on_click=open_playlist(pid), ink=True,
            bgcolor=card_fill())

    def create_playlist_flow(ev=None):
        def created(name):
            create_playlist(name)
            toast(page, f"Создан плейлист «{name}»")
            refresh_library()
            persist()
            page.update()
        ask_name("Новый плейлист", "", created, "Создать")

    def build_track_row(pid, tracks, i, t, queue_name):
        async def play_it(ev):
            await play_queue(tracks, i, queue_name)
            refresh_library()
            persist()

        def more(ev):
            open_track_actions(t, pid)

        cur = current_track()
        is_cur = cur is not None and cur.id == t.id
        heart = make_icon("favorite", size=14, color="#ff5d7a") if t.id in p["favorites"] \
            else ft.Container(width=0)
        return ft.Container(
            content=ft.Row([
                thumb_box(t, 46),
                ft.Column([
                    ft.Row([ft.Text(t.title, size=14, weight=ft.FontWeight.W_600,
                                    color="white", max_lines=1,
                                    overflow=ft.TextOverflow.ELLIPSIS, expand=True), heart]),
                    ft.Text(t.artist, size=12, color="white70", max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS)], expand=True, spacing=2),
                ft.IconButton(icon=icon_value("more_vert"), icon_color="white70",
                              tooltip="Действия", on_click=more),
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=0),
            padding=ft.Padding(8, 4, 0, 4), border_radius=radius(18),
            on_click=play_it, ink=True,
            bgcolor=(ft.Colors.with_opacity(0.35, p["accent_now"]) if is_cur
                     else card_fill()))

    def rename_flow(pid):
        def handler(ev):
            pl = get_playlist(pid)
            if not pl:
                return

            def renamed(name):
                pl["name"] = name[:60]
                refresh_library()
                persist()
                page.update()
            ask_name("Переименовать плейлист", pl["name"], renamed)
        return handler

    def delete_flow(pid):
        def handler(ev):
            def confirm(e2):
                close_dialog(page, dlg)
                p["playlists"] = [pl for pl in p["playlists"] if pl["id"] != pid]
                p["lib_view"] = None
                refresh_library()
                persist()
                page.update()

            dlg = ft.AlertDialog(
                title=ft.Text("Удалить плейлист?"),
                content=ft.Text(f"«{playlist_name(pid)}» будет удалён. Сами треки останутся "
                                "в библиотеке."),
                actions=[ft.TextButton("Удалить", on_click=confirm),
                         ft.TextButton("Отмена", on_click=lambda e2: close_dialog(page, dlg))])
            open_dialog(page, dlg)
        return handler

    def visible_tracks(pid):
        """Треки с учётом сортировки и поиска в библиотеке."""
        base = tracks_of(pid)
        if pid in (RECENT, TOP):               # у них свой порядок
            return logic.filter_tracks(base, p["lib_query"])
        return logic.filter_tracks(logic.sort_tracks(base, p["sort_mode"]), p["lib_query"])

    def build_library_top():
        pid = p["lib_view"]
        if pid is None:
            add_btn = ft.Container(make_icon("add", size=24, color="white"), width=44,
                                   height=44, alignment=CENTER, border=btn_edge(True),
                                   bgcolor=btn_fill(True), border_radius=radius(22),
                                   on_click=create_playlist_flow, ink=True,
                                   tooltip="Новый плейлист")
            lib_top.content = ft.Column([
                ft.Row([title_text("Библиотека"), add_btn], alignment=SPACE_BETWEEN),
                ft.Row([ghost_btn("Добавить файлы", pick_local, "folder_open")]),
            ], spacing=10)
            return
        custom = pid not in SPECIAL
        tracks = visible_tracks(pid)

        async def play_all(ev):
            await play_queue(tracks, 0, playlist_name(pid))
            refresh_library()
            persist()

        async def play_shuffled(ev):
            await play_queue(random.sample(tracks, len(tracks)), 0, playlist_name(pid))
            refresh_library()
            persist()

        def cycle_sort(ev):
            p["sort_mode"] = logic.next_sort_mode(p["sort_mode"])
            refresh_library()
            persist()
            page.update()

        header = [ft.IconButton(icon=icon_value("arrow_back"), icon_color="white",
                                on_click=back_to_overview),
                  ft.Container(title_text(playlist_name(pid), 24), expand=True)]
        if custom:
            header += [ft.IconButton(icon=icon_value("edit"), icon_color="white70",
                                     tooltip="Переименовать", on_click=rename_flow(pid)),
                       ft.IconButton(icon=icon_value("delete"), icon_color="white70",
                                     tooltip="Удалить плейлист", on_click=delete_flow(pid))]
        toolbar = [ghost_btn("Играть", play_all, "play_arrow", disabled=not tracks),
                   ghost_btn("Перемешать", play_shuffled, "shuffle", disabled=not tracks)]
        if pid not in (RECENT, TOP):
            toolbar.append(ghost_btn(logic.sort_label(p["sort_mode"]).capitalize(),
                                     cycle_sort, "sort"))
        lib_top.content = ft.Column([
            ft.Row(header, spacing=0),
            ft.Text(f"Треков: {len(tracks)}", size=12, color="white70"),
            ft.Row(toolbar, wrap=True, spacing=8, run_spacing=8),
        ], spacing=8)

    def build_library_body():
        pid, query = p["lib_view"], p["lib_query"].strip()
        rows = []
        if pid is None and not query:
            cards = [playlist_card(x, SPECIAL[x], len(tracks_of(x)))
                     for x in (ALL, FAV, RECENT, TOP)]
            cards += [playlist_card(pl["id"], pl["name"], len(pl["tracks"]))
                      for pl in p["playlists"]]
            if not p["playlists"]:
                cards.append(ft.Text("Создай плейлист кнопкой «+», а треки добавляй через "
                                     "меню ⋮ у любого трека.", size=12, color="white54"))
            rows = cards
        else:
            src = ALL if pid is None else pid
            tracks = visible_tracks(src)
            name = (f"Поиск: {query}" if pid is None else playlist_name(pid))
            rows = [build_track_row(src, tracks, i, t, name) for i, t in enumerate(tracks)]
            if not rows:
                rows = [ft.Text("Ничего не найдено." if query else {
                    ALL: "Здесь появятся все добавленные треки.",
                    FAV: "Нажми сердце у трека, и он появится здесь.",
                    RECENT: "Здесь будут треки, которые ты слушал.",
                    TOP: "Здесь будут самые частые треки."}.get(
                        src, "Плейлист пуст. Добавь треки через меню ⋮."),
                    size=13, color="white70")]
        lib_body.content = ft.ListView(rows, expand=True, spacing=8)

    def refresh_library():
        pid = p["lib_view"]
        if pid is not None and pid not in SPECIAL and get_playlist(pid) is None:
            p["lib_view"] = None
        build_library_top()
        build_library_body()

    # ======================= Экран «Настройки» =======================
    bg_layer = ft.Container(expand=True, bgcolor="#14142b")
    bg_state = {"img": None}
    bg_url_field = ft.TextField(hint_text="Ссылка на картинку или GIF", expand=True,
                                text_size=13, **field_style())

    def fit_background():
        img = bg_state["img"]
        if img is not None:
            img.width = page.width or 420
            img.height = page.height or 820

    def apply_background():
        bg = p["background"]
        bg_state["img"] = None
        try:
            if bg["type"] in ("file", "url"):
                img = make_bg_image(bg["value"], bg["type"] == "url",
                                    page.width or 420, page.height or 820)
                bg_state["img"] = img
                bg_layer.content = img
            else:
                bg_layer.content = None
                bg_layer.bgcolor = bg["value"]
                page.bgcolor = bg["value"]
        except Exception as err:
            bg_state["img"] = None
            bg_layer.content = None
            toast(page, f"Не удалось показать фон: {err}")

    def fit_art():
        size = int(max(200, min(300, (page.width or 420) - 72)))
        if size != p["art"]:
            p["art"] = size
            art_holder.width = art_holder.height = size
            if art_switcher.content is not art_icon:
                art_switcher.content.width = art_switcher.content.height = size

    def on_resize(e):
        fit_background()
        fit_art()
        page.update()

    page.on_resize = on_resize
    try:
        page.on_resized = on_resize
    except Exception:
        pass

    async def pick_background(e):
        files = await picker.pick_files(
            dialog_title="Выбери картинку или GIF", allow_multiple=False,
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["gif", "png", "jpg", "jpeg", "webp"])
        if not files or not files[0].path:
            return
        src = files[0].path
        try:
            os.makedirs(BG_DIR, exist_ok=True)
            for old in os.listdir(BG_DIR):
                os.remove(os.path.join(BG_DIR, old))
            dst = os.path.join(BG_DIR, f"bg_{int(time.time())}{os.path.splitext(src)[1].lower()}")
            shutil.copyfile(src, dst)
        except OSError as err:
            toast(page, f"Не удалось сохранить фон: {err}")
            return
        p["background"] = {"type": "file", "value": dst}
        apply_background()
        persist()
        if os.path.getsize(dst) > 20 * 1024 * 1024:
            toast(page, "Файл больше 20 МБ, приложение может работать медленнее")
        page.update()

    def apply_url_background(e):
        url = (bg_url_field.value or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            toast(page, "Нужна ссылка, начинающаяся с http:// или https://")
            return
        p["background"] = {"type": "url", "value": url}
        apply_background()
        persist()
        page.update()

    def set_bg_color(color):
        def handler(e):
            p["background"] = {"type": "color", "value": color}
            apply_background()
            persist()
            page.update()
        return handler

    def swatch_row(colors, on_pick):
        return ft.Row([ft.Container(width=38, height=38, bgcolor=c, border_radius=radius(19),
                                    border=border_all(1, "white38"), on_click=on_pick(c),
                                    ink=True) for c in colors],
                      spacing=10, wrap=True)

    def open_color_picker(title, current, on_live, on_commit, on_cancel):
        """Свой цвет: оттенок / насыщенность / яркость, HEX и готовые цвета.
        Цвет применяется сразу (предпросмотр), «Отмена» возвращает прежний."""
        cur = logic.normalize_hex(current) or "#7C5CFF"
        h, s, v = logic.hex_to_hsv(cur)
        chosen = {"hex": cur}
        preview = ft.Container(height=56, border_radius=radius(16), bgcolor=cur)
        hex_field = ft.TextField(label="HEX", value=cur, width=140, text_size=14)
        h_sl = ft.Slider(min=0, max=360, value=h)
        s_sl = ft.Slider(min=0, max=100, value=s)
        v_sl = ft.Slider(min=0, max=100, value=v)

        def set_color(hex_value, from_sliders=False, from_field=False):
            chosen["hex"] = hex_value
            preview.bgcolor = hex_value
            if not from_field:
                hex_field.value = hex_value
            if not from_sliders:
                h_sl.value, s_sl.value, v_sl.value = logic.hex_to_hsv(hex_value)
            on_live(hex_value)
            page.update()

        def on_sliders(e):
            set_color(logic.hsv_to_hex(h_sl.value, s_sl.value, v_sl.value), from_sliders=True)

        def on_hex(e):
            norm = logic.normalize_hex(hex_field.value)
            if norm:
                set_color(norm, from_field=True)

        def preset(color):
            return lambda e: set_color(color)

        for sl in (h_sl, s_sl, v_sl):
            sl.on_change = on_sliders
        hex_field.on_change = on_hex
        swatches_row = ft.Row(
            [ft.Container(width=32, height=32, bgcolor=c, border_radius=radius(16),
                          border=border_all(1, "white38"), on_click=preset(c), ink=True)
             for c in logic.PALETTE], wrap=True, spacing=8, run_spacing=8)

        def apply(e):
            close_dialog(page, dlg)
            on_commit(chosen["hex"])

        def cancel(e):
            close_dialog(page, dlg)
            on_cancel()

        dlg = ft.AlertDialog(
            modal=True, title=ft.Text(title),
            content=ft.Container(ft.Column([
                preview,
                ft.Text("Оттенок", size=12), h_sl,
                ft.Text("Насыщенность", size=12), s_sl,
                ft.Text("Яркость", size=12), v_sl,
                hex_field,
                ft.Text("Готовые цвета", size=12), swatches_row,
            ], tight=True, scroll=ft.ScrollMode.AUTO, spacing=4), width=330, height=520),
            actions=[ft.TextButton("Применить", on_click=apply),
                     ft.TextButton("Отмена", on_click=cancel)])
        open_dialog(page, dlg)

    def pick_bg_color(e=None):
        old = dict(p["background"])

        def live(hex_value):
            p["background"] = {"type": "color", "value": hex_value}
            apply_background()

        def commit(hex_value):
            live(hex_value)
            persist()
            page.update()

        def cancel():
            p["background"] = old
            apply_background()
            page.update()

        open_color_picker("Цвет фона", old["value"] if old["type"] == "color" else "#14142b",
                          live, commit, cancel)

    def pick_accent_custom(e=None):
        old = p["accent"]

        def live(hex_value):
            p["accent"] = p["accent_now"] = hex_value
            apply_accent()

        def commit(hex_value):
            live(hex_value)
            spawn(refresh_accent, current_track())
            persist()
            page.update()

        def cancel():
            live(old)
            page.update()

        open_color_picker("Акцентный цвет", old, live, commit, cancel)

    def pick_panel_tint(e=None):
        old = p["panel_tint"]

        def live(hex_value):
            p["panel_tint"] = hex_value
            for panel in glass_panels:
                panel.bgcolor = panel_color()
            nav.bgcolor = nav_color()

        def commit(hex_value):
            live(hex_value)
            persist()
            page.update()

        def cancel():
            live(old)
            page.update()

        open_color_picker("Цвет панелей", old, live, commit, cancel)

    def pick_viz_color(e=None):
        old = p["viz_color"]

        def live(hex_value):
            p["viz_color"] = hex_value
            apply_viz_colors()

        def commit(hex_value):
            live(hex_value)
            persist()
            page.update()

        def cancel():
            live(old)
            page.update()

        open_color_picker("Цвет эквалайзера", old or p["accent_now"], live, commit, cancel)

    def reset_viz_color(e=None):
        p["viz_color"] = ""
        apply_viz_colors()
        persist()
        page.update()

    def labeled_switch(label, value, on_change):
        return ft.Row([ft.Text(label, size=13, color="white", expand=True),
                       ft.Switch(value=value, on_change=on_change)])

    background_card = glass(ft.Column([
        ft.Text("Фон", size=16, weight=ft.FontWeight.BOLD, color="white"),
        ghost_btn("Выбрать картинку или GIF", pick_background, "image", register_btn=True),
        ft.Row([bg_url_field, ft.IconButton(icon=icon_value("check"), icon_color="white",
                                            on_click=apply_url_background)]),
        ft.Text("Или просто цвет:", size=12, color="white70"),
        swatch_row(COLOR_CHOICES, set_bg_color),
        ghost_btn("Свой цвет фона…", pick_bg_color, "palette", register_btn=True),
    ], tight=True, spacing=10))

    # ----- оформление -----
    def update_corners(e):
        p["button_radius"] = float(e.control.value)
        restyle_buttons()
        persist()
        page.update()

    def update_panel_opacity(e):
        p["panel_opacity"] = float(e.control.value) / 100
        for panel in glass_panels:
            panel.bgcolor = panel_color()
        nav.bgcolor = nav_color()
        persist()
        page.update()

    def update_button_opacity(e):
        p["button_opacity"] = float(e.control.value) / 100
        restyle_buttons()
        refresh_library()
        render_results()
        persist()
        page.update()

    def pick_accent(color):
        def handler(e):
            p["accent"] = color
            p["accent_now"] = color
            apply_accent()
            spawn(refresh_accent, current_track())
            persist()
            page.update()
        return handler

    def on_dynamic_accent(e):
        p["dynamic_accent"] = bool(e.control.value)
        if p["dynamic_accent"] and PILImage is None:
            toast(page, "Для этого нужен Pillow: pip install pillow")
        p["accent_now"] = p["accent"]
        apply_accent()
        spawn(refresh_accent, current_track())
        persist()
        page.update()

    def on_visualizer(e):
        set_visualizer(bool(e.control.value))

    corners_slider = ft.Slider(min=0, max=36, value=p["button_radius"],
                               label="Скругление: {value}", active_color=p["accent"],
                               on_change=update_corners)
    btn_opacity_slider = ft.Slider(min=0, max=60, value=round(p["button_opacity"] * 100),
                                   label="Заливка кнопок: {value}%", active_color=p["accent"],
                                   on_change=update_button_opacity)
    panel_opacity_slider = ft.Slider(min=10, max=95, value=round(p["panel_opacity"] * 100),
                                     label="Непрозрачность: {value}%",
                                     active_color=p["accent"], on_change=update_panel_opacity)
    accent_sliders += [corners_slider, btn_opacity_slider, panel_opacity_slider]

    def reset_style(e=None):
        p["accent"] = p["accent_now"] = "#7C5CFF"
        p["panel_tint"], p["viz_color"] = "#000000", ""
        p["button_opacity"], p["panel_opacity"], p["button_radius"] = 0.08, 0.65, 16.0
        btn_opacity_slider.value, panel_opacity_slider.value, corners_slider.value = 8, 65, 16
        for panel in glass_panels:
            panel.bgcolor = panel_color()
        nav.bgcolor = nav_color()
        apply_accent()
        refresh_library()
        render_results()
        persist()
        toast(page, "Оформление сброшено")
        page.update()

    style_card = glass(ft.Column([
        ft.Text("Оформление", size=16, weight=ft.FontWeight.BOLD, color="white"),
        ft.Text("Акцентный цвет", size=12, color="white70"),
        swatch_row(ACCENTS, pick_accent),
        ft.Row([ghost_btn("Свой цвет акцента…", pick_accent_custom, "palette", register_btn=True),
                ghost_btn("Цвет панелей…", pick_panel_tint, "format_paint", register_btn=True)],
               wrap=True, spacing=8, run_spacing=8),
        labeled_switch("Цвет из обложки", p["dynamic_accent"], on_dynamic_accent),
        ft.Text("Прозрачность кнопок (0% = только контур)", size=12, color="white70"),
        btn_opacity_slider,
        ft.Text("Скругление кнопок", size=12, color="white70"), corners_slider,
        ft.Text("Непрозрачность панелей", size=12, color="white70"), panel_opacity_slider,
        ft.Text("Эквалайзер", size=14, weight=ft.FontWeight.BOLD, color="white"),
        ft.Row([ft.Text("Показывать", size=13, color="white", expand=True), viz_switch]),
        viz_chips_row,
        ft.Row([ghost_btn("Цвет эквалайзера…", pick_viz_color, "palette", register_btn=True),
                ghost_btn("Как акцент", reset_viz_color, "restart_alt", register_btn=True)],
               wrap=True, spacing=8, run_spacing=8),
        ghost_btn("Сбросить оформление", reset_style, "restore", register_btn=True),
    ], tight=True, spacing=6))

    # ----- резервная копия -----
    async def export_playlists(e):
        if not p["playlists"]:
            toast(page, "Нет плейлистов для экспорта")
            return
        data = logic.build_export({tid: t.to_dict() for tid, t in p["tracks"].items()},
                                  p["playlists"])
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        fallback = os.path.join(EXPORT_DIR, f"wextly_playlists_{int(time.time())}.json")
        path = None
        try:
            kwargs = dict(dialog_title="Сохранить плейлисты", file_name="wextly_playlists.json",
                          allowed_extensions=["json"])
            if IS_MOBILE:
                kwargs["src_bytes"] = payload    # на телефоне окно само записывает файл
            path = await picker.save_file(**kwargs)
            if not path:
                return                           # окно закрыли без сохранения
            if IS_MOBILE:
                toast(page, "Плейлисты сохранены")
                return
        except Exception:
            path = fallback                      # окно сохранения недоступно
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "wb") as f:
                f.write(payload)
            toast(page, f"Плейлисты сохранены: {path}")
        except OSError as err:
            toast(page, f"Не удалось сохранить файл: {err}")

    async def import_playlists(e):
        files = await picker.pick_files(
            dialog_title="Выбери файл плейлистов", allow_multiple=False,
            file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=["json"])
        if not files or not files[0].path:
            return
        try:
            with open(files[0].path, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw = {tid: t.to_dict() for tid, t in p["tracks"].items()}
            n_pl, n_tr = logic.merge_import(
                data, raw, p["playlists"],
                track_ok=lambda d: d.get("source") != "local" or os.path.exists(d.get("path", "")))
            for tid, d in raw.items():
                if tid not in p["tracks"]:
                    p["tracks"][tid] = Track.from_dict(d)
        except (OSError, ValueError) as err:
            toast(page, f"Не удалось импортировать: {err}")
            return
        refresh_library()
        persist()
        toast(page, f"Импортировано плейлистов: {n_pl}, новых треков: {n_tr}")
        page.update()

    def clear_cache(e):
        for d in repo.cache_dirs:
            shutil.rmtree(d, ignore_errors=True)
        toast(page, "Кэш скачанных треков очищен")

    # ----- Telegram -----
    async def open_telegram(e):
        tg = repo.telegram
        if not tg.available:
            toast(page, "Сначала установи Telethon: pip install telethon")
            return
        cfg = tg.load_config()
        api_id_f = ft.TextField(label="api_id", value=str(cfg.get("api_id", "")))
        api_hash_f = ft.TextField(label="api_hash", value=cfg.get("api_hash", ""))
        chat_f = ft.TextField(label="Канал/чат: @имя или t.me/имя", value=cfg.get("chat", ""))
        phone_f = ft.TextField(label="Телефон (+380...)", value=cfg.get("phone", ""))
        code_f = ft.TextField(label="Код из Telegram", visible=False)
        pw_f = ft.TextField(label="Пароль двухэтапной защиты", password=True,
                            can_reveal_password=True, visible=False)
        status = ft.Text("Данные берутся на my.telegram.org → API development tools", size=12)
        phase = {"v": "connect"}

        async def finish():
            status.value = "Загружаю список треков…"
            page.update()
            tracks = await tg.list_tracks(limit=50)
            if not tracks:
                status.value = "В этом канале/чате не найдено музыки."
                page.update()
                return
            register(tracks)
            name = f"Telegram · {chat_f.value.strip().lstrip('@')}"[:60]
            pl = next((x for x in p["playlists"] if x["name"] == name), None) \
                or create_playlist(name)
            pl["tracks"] = [t.id for t in tracks]
            if not p["queue"]:
                p["queue"], p["queue_name"], p["index"] = list(tracks), name, 0
                p["need_load"] = True
                show_track_info()
            close_dialog(page, dlg)
            toast(page, f"Создан плейлист «{name}»: {len(tracks)} треков")
            refresh_library()
            persist()
            page.update()

        async def on_next(ev):
            try:
                if phase["v"] == "connect":
                    tg.save_config({"api_id": api_id_f.value.strip(),
                                    "api_hash": api_hash_f.value.strip(),
                                    "chat": chat_f.value.strip(),
                                    "phone": phone_f.value.strip()})
                    status.value = "Подключаюсь…"
                    page.update()
                    if await tg.connect():
                        await finish()
                    else:
                        await tg.send_code(phone_f.value.strip())
                        code_f.visible = True
                        phase["v"] = "code"
                        status.value = "Код отправлен в Telegram. Введи его и нажми «Далее»."
                elif phase["v"] == "code":
                    result = await tg.sign_in(phone_f.value.strip(), code_f.value.strip())
                    if result == "password":
                        pw_f.visible = True
                        phase["v"] = "password"
                        status.value = "Нужен пароль двухэтапной защиты."
                    else:
                        await finish()
                else:
                    await tg.sign_in_password(pw_f.value)
                    await finish()
            except Exception as err:
                status.value = f"Ошибка: {err}"
            page.update()

        dlg = ft.AlertDialog(
            title=ft.Text("Telegram"),
            content=ft.Container(
                ft.Column([api_id_f, api_hash_f, chat_f, phone_f, code_f, pw_f, status],
                          tight=True, scroll=ft.ScrollMode.AUTO), width=320),
            actions=[ft.TextButton("Далее", on_click=on_next),
                     ft.TextButton("Отмена", on_click=lambda ev: close_dialog(page, dlg))])
        open_dialog(page, dlg)

    data_card = glass(ft.Column([
        ft.Text("Данные и источники", size=16, weight=ft.FontWeight.BOLD, color="white"),
        ft.Row([ghost_btn("Экспорт плейлистов", export_playlists, "upload", register_btn=True),
                ghost_btn("Импорт плейлистов", import_playlists, "download", register_btn=True),
                ghost_btn("Telegram", open_telegram, "cloud", register_btn=True),
                ghost_btn("Очистить кэш", clear_cache, "delete_sweep", register_btn=True)],
               wrap=True, spacing=8, run_spacing=8),
        ft.Text("Горячие клавиши на экране «Плеер»: Пробел - пауза, ←/→ - перемотка 10 с, "
                "Shift+←/→ - пред./след., ↑/↓ - громкость, L - лайк, S - shuffle, "
                "R - повтор. Ctrl+F - поиск по библиотеке.", size=11, color="white54"),
    ], tight=True, spacing=10))

    settings_view = ft.Column(
        [title_text("Настройки"), background_card, style_card, data_card],
        expand=True, spacing=12, scroll=ft.ScrollMode.AUTO)

    # ======================= Навигация, мини-плеер, клавиатура =======================
    views = [player_view, search_view, lib_holder, settings_view]
    view_switcher = make_switcher(player_view, 220, expand=True)
    view_holder = ft.Container(content=view_switcher, expand=True,
                               padding=ft.Padding(16, 16, 16, 0))

    def go_to(idx: int):
        p["view"] = idx
        view_switcher.content = views[idx]
        nav.selected_index = idx
        if idx == 2:
            refresh_library()
        render_mini()
        page.update()

    mini_bar = glass(ft.Row([
        ft.Container(make_icon("music_note", size=22, color="white70"),
                     width=40, height=40, alignment=CENTER, border_radius=radius(12),
                     bgcolor=ft.Colors.with_opacity(0.15, "white")),
        ft.Container(ft.Column([mini_title, mini_artist], spacing=0, tight=True),
                     expand=True, on_click=lambda e: go_to(0)),
        ft.Container(mini_play_icon, width=44, height=44, alignment=CENTER,
                     border_radius=radius(22), on_click=toggle_play, ink=True),
    ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=8, corner=20, margin=ft.Margin(16, 0, 16, 8))
    mini_bar.visible = False

    nav = ft.NavigationBar(
        selected_index=0,
        bgcolor=nav_color(),
        indicator_color=ft.Colors.with_opacity(0.45, p["accent"]),
        destinations=[
            ft.NavigationBarDestination(icon=icon_value("play_circle"), label="Плеер"),
            ft.NavigationBarDestination(icon=icon_value("search"), label="Поиск"),
            ft.NavigationBarDestination(icon=icon_value("library_music"), label="Библиотека"),
            ft.NavigationBarDestination(icon=icon_value("tune"), label="Настройки"),
        ],
        on_change=lambda e: go_to(int(e.control.selected_index)))

    async def on_key(e):
        key = str(getattr(e, "key", "")).lower()
        if getattr(e, "ctrl", False) and key == "f":
            go_to(2)
            return
        if p["view"] != 0:         # на других экранах печатаем текст, клавиши не трогаем
            return
        shift = bool(getattr(e, "shift", False))
        if key in (" ", "space"):
            await toggle_play()
        elif key == "arrow right":
            await (next_track() if shift else seek_by(10000))
        elif key == "arrow left":
            await (prev_track() if shift else seek_by(-10000))
        elif key == "arrow up":
            await nudge_volume(0.05)
        elif key == "arrow down":
            await nudge_volume(-0.05)
        elif key == "l":
            toggle_favorite()
        elif key == "s":
            toggle_shuffle()
        elif key == "r":
            toggle_repeat()

    page.on_keyboard_event = on_key

    page.add(ft.Stack([
        bg_layer,
        ft.Container(bgcolor=ft.Colors.with_opacity(0.25, "black"), expand=True),
        make_safe(ft.Column([view_holder, mini_bar, nav], expand=True, spacing=0)),
    ], expand=True))

    # ======================= Запуск =======================
    apply_background()
    apply_accent()
    render_play()
    show_track_info()
    refresh_library()
    update_time_labels()
    page.update()
    spawn(progress_loop)
    if p["visualizer"]:
        spawn(viz_loop)
    if p["dynamic_accent"]:
        spawn(refresh_accent, current_track())


if __name__ == "__main__":
    ft.run(main)
