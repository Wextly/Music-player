"""Гибридный репозиторий треков: локальные файлы, Telegram, SoundCloud."""
import asyncio
import glob
import io
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass

try:
    import mutagen
except ImportError:
    mutagen = None

try:
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    from telethon.tl.types import InputMessagesFilterMusic
    TELETHON_OK = True
except ImportError:
    TELETHON_OK = False

try:
    import yt_dlp
    YTDLP_OK = True
except ImportError:
    YTDLP_OK = False

try:
    from PIL import Image as PILImage
except ImportError:
    PILImage = None

NO_ARTIST = "Неизвестный исполнитель"


@dataclass
class Track:
    id: str
    title: str
    artist: str = NO_ARTIST
    source: str = "local"      # "local", "telegram" или "soundcloud"
    path: str = ""             # локальные файлы
    chat: str = ""             # Telegram
    msg_id: int = 0
    url: str = ""              # SoundCloud
    thumb: str = ""            # ссылка на обложку
    ext: str = ".mp3"
    duration: int = 0          # секунды, если известна

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Track":
        known = Track.__dataclass_fields__.keys()
        return Track(**{k: v for k, v in d.items() if k in known})


def read_tags(path: str):
    title = os.path.splitext(os.path.basename(path))[0]
    artist = NO_ARTIST
    if mutagen is not None:
        try:
            tags = mutagen.File(path, easy=True)
            if tags:
                title = (tags.get("title") or [title])[0]
                artist = (tags.get("artist") or [artist])[0]
        except Exception as err:
            print(f">>> Не удалось прочитать теги {path}: {err}")
    return title, artist


def read_duration(path: str) -> int:
    """Длительность файла в секундах (0, если неизвестна). Нужен mutagen."""
    if mutagen is not None:
        try:
            f = mutagen.File(path)
            if f is not None and f.info is not None:
                return int(f.info.length or 0)
        except Exception as err:
            print(f">>> Не удалось определить длительность {path}: {err}")
    return 0


class LocalSource:
    @staticmethod
    def tracks_from_paths(paths) -> list:
        tracks = []
        for path in paths:
            title, artist = read_tags(path)
            tracks.append(Track(
                id=f"local:{path}", title=title, artist=artist, source="local",
                path=path, ext=os.path.splitext(path)[1] or ".mp3",
                duration=read_duration(path)))
        return tracks


class SoundCloudSource:
    """Поиск и загрузка через yt-dlp (pip install yt-dlp)."""

    def __init__(self, data_dir: str):
        self.cache_dir = os.path.join(data_dir, "sc_cache")

    @property
    def available(self) -> bool:
        return YTDLP_OK

    @staticmethod
    def _to_track(e: dict, default_url: str = ""):
        url = e.get("webpage_url") or e.get("url") or default_url
        eid = e.get("id")
        if not url or eid is None:
            return None
        thumbs = e.get("thumbnails") or []
        thumb = e.get("thumbnail") or (thumbs[-1].get("url") if thumbs else "") or ""
        return Track(
            id=f"sc:{eid}", title=e.get("title") or "Без названия",
            artist=e.get("uploader") or e.get("channel") or NO_ARTIST,
            source="soundcloud", url=url, thumb=thumb,
            duration=int(e.get("duration") or 0))

    def _extract_sync(self, target: str, limit=None) -> list:
        opts = {"quiet": True, "no_warnings": True, "extract_flat": True,
                "skip_download": True}
        if limit:
            opts["playlistend"] = limit
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=False)
        if not info:
            return []
        entries = info.get("entries")
        items = list(entries) if entries is not None else [info]
        tracks = []
        for e in items:
            if e:
                t = self._to_track(e, target if entries is None else "")
                if t:
                    tracks.append(t)
        return tracks

    async def search(self, query: str, limit: int = 15) -> list:
        return await asyncio.to_thread(self._extract_sync, f"scsearch{limit}:{query}")

    async def from_url(self, url: str) -> list:
        """Трек или плейлист (sets) по ссылке soundcloud.com."""
        return await asyncio.to_thread(self._extract_sync, url.strip(), 100)

    def _related_sync(self, track: Track, limit: int) -> list:
        if track.url:
            base = track.url.split("?")[0].rstrip("/")
            try:
                found = self._extract_sync(base + "/recommended", limit)
                if found:
                    return found
            except Exception as err:
                print(f">>> Рекомендации SoundCloud недоступны: {err}")
        # запасной вариант: поиск по автору и названию
        query = track.title if track.artist in (NO_ARTIST, "Telegram") \
            else f"{track.artist} {track.title}"
        return self._extract_sync(f"scsearch{limit}:{query}")

    async def related(self, track: Track, limit: int = 20) -> list:
        return await asyncio.to_thread(self._related_sync, track, limit)

    def _cached_file(self, track_id: str):
        safe = re.sub(r"[^\w-]", "_", track_id)
        files = [f for f in glob.glob(os.path.join(self.cache_dir, f"{safe}.*"))
                 if not f.endswith((".part", ".ytdl", ".temp"))]
        return safe, (files[0] if files else None)

    def _download_sync(self, track: Track) -> str:
        os.makedirs(self.cache_dir, exist_ok=True)
        safe, cached = self._cached_file(track.id)
        if cached:
            return cached
        opts = {
            "quiet": True, "no_warnings": True, "noplaylist": True,
            # сначала обычные http-файлы, потом всё остальное
            "format": "bestaudio[protocol=https]/bestaudio[protocol=http]/bestaudio/best",
            "outtmpl": os.path.join(self.cache_dir, f"{safe}.%(ext)s"),
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(track.url, download=True)
        if info:
            track.thumb = info.get("thumbnail") or track.thumb
        _, cached = self._cached_file(track.id)
        if not cached:
            raise RuntimeError("SoundCloud не отдал аудиофайл")
        return cached

    async def resolve(self, track: Track) -> str:
        return await asyncio.to_thread(self._download_sync, track)


class TelegramSource:
    def __init__(self, data_dir: str):
        self.config_path = os.path.join(data_dir, "telegram_config.json")
        self.session_path = os.path.join(data_dir, "tg_session")
        self.cache_dir = os.path.join(data_dir, "tg_cache")
        self.client = None
        self._client_key = None

    @property
    def available(self) -> bool:
        return TELETHON_OK

    def load_config(self) -> dict:
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save_config(self, cfg: dict) -> None:
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except OSError as err:
            print(f">>> Не удалось сохранить настройки Telegram: {err}")

    def _chat(self):
        chat = str(self.load_config().get("chat", "")).strip()
        chat = re.sub(r"^https?://t\.me/", "", chat).lstrip("@").strip("/")
        if not chat:
            raise ValueError("Не указан канал или чат")
        return int(chat) if chat.lstrip("-").isdigit() else chat

    async def connect(self) -> bool:
        cfg = self.load_config()
        api_id, api_hash = int(cfg["api_id"]), str(cfg["api_hash"]).strip()
        key = (api_id, api_hash)
        if self.client is None or key != self._client_key:
            self.client = TelegramClient(self.session_path, api_id, api_hash)
            self._client_key = key
        if not self.client.is_connected():
            await self.client.connect()
        return await self.client.is_user_authorized()

    async def send_code(self, phone: str) -> None:
        await self.client.send_code_request(phone)

    async def sign_in(self, phone: str, code: str) -> str:
        try:
            await self.client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            return "password"
        return "ok"

    async def sign_in_password(self, password: str) -> None:
        await self.client.sign_in(password=password)

    async def list_tracks(self, limit: int = 50) -> list:
        chat = self._chat()
        tracks = []
        async for msg in self.client.iter_messages(
                chat, limit=limit, filter=InputMessagesFilterMusic):
            f = msg.file
            if f is None:
                continue
            name = os.path.splitext(f.name or "")[0] or f"Трек {msg.id}"
            tracks.append(Track(
                id=f"tg:{chat}:{msg.id}",
                title=getattr(f, "title", None) or name,
                artist=getattr(f, "performer", None) or "Telegram",
                source="telegram", chat=str(chat), msg_id=msg.id,
                ext=f.ext or ".mp3", duration=int(f.duration or 0)))
        return tracks

    async def resolve(self, track: Track) -> str:
        os.makedirs(self.cache_dir, exist_ok=True)
        safe_chat = re.sub(r"[^\w-]", "_", track.chat)
        final = os.path.join(self.cache_dir, f"{safe_chat}_{track.msg_id}{track.ext}")
        if os.path.exists(final) and os.path.getsize(final) > 0:
            return final
        if self.client is None or not self.client.is_connected():
            if not await self.connect():
                raise RuntimeError("Telegram не подключён: открой Настройки → Telegram")
        chat = int(track.chat) if track.chat.lstrip("-").isdigit() else track.chat
        msg = await self.client.get_messages(chat, ids=track.msg_id)
        if not msg:
            raise RuntimeError("Сообщение с треком не найдено")
        tmp = final + ".part"
        await self.client.download_media(msg, file=tmp)
        os.replace(tmp, final)
        return final


class TrackRepository:
    """Единая точка доступа: плееру всё равно, откуда трек."""

    def __init__(self, data_dir: str):
        self.local = LocalSource()
        self.telegram = TelegramSource(data_dir)
        self.soundcloud = SoundCloudSource(data_dir)
        self.cache_dirs = [self.telegram.cache_dir, self.soundcloud.cache_dir]

    async def resolve(self, track: Track) -> str:
        if track.source == "telegram":
            return await self.telegram.resolve(track)
        if track.source == "soundcloud":
            return await self.soundcloud.resolve(track)
        if not os.path.exists(track.path):
            raise FileNotFoundError(f"Файл не найден: {track.path}")
        return track.path


def fetch_lyrics(artist: str, title: str, timeout: int = 10) -> str:
    """Текст песни через бесплатный сервис lyrics.ovh (может не знать трек)."""
    url = "https://api.lyrics.ovh/v1/{}/{}".format(
        urllib.parse.quote(artist.strip(), safe=""), urllib.parse.quote(title.strip(), safe=""))
    req = urllib.request.Request(url, headers={"User-Agent": "WextlyPlayer/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as err:
        raise LookupError(f"Текст не найден ({err})")
    text = (data.get("lyrics") or "").strip()
    if not text:
        raise LookupError("Текст не найден")
    return text


def dominant_color(url: str):
    """Средний цвет обложки (r, g, b) с упором на насыщенные пиксели.
    Нужен Pillow (pip install pillow); без него возвращает None."""
    if PILImage is None or not url.lower().startswith(("http://", "https://")):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "WextlyPlayer/1.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read(5_000_000)
        img = PILImage.open(io.BytesIO(raw)).convert("RGB").resize((24, 24))
    except Exception as err:
        print(f">>> Не удалось получить цвет обложки: {err}")
        return None
    r = g = b = wsum = 0.0
    for pr, pg, pb in img.getdata():
        sat = max(pr, pg, pb) - min(pr, pg, pb)
        w = 1.0 + sat            # насыщенные пиксели важнее серых
        r, g, b, wsum = r + pr * w, g + pg * w, b + pb * w, wsum + w
    return (int(r / wsum), int(g / wsum), int(b / wsum)) if wsum else None
