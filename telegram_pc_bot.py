from __future__ import annotations

import argparse
import asyncio
import ctypes
import html
import io
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import winreg
from dataclasses import dataclass
from pathlib import Path

import psutil
import pystray
from PIL import Image, ImageDraw, ImageGrab
from telethon import Button, TelegramClient, connection, events
from telethon.errors import MessageNotModifiedError
from telethon.tl.custom.message import Message


APP_NAME = "Telegram PC Control Proxy"
AUTOSTART_KEY_NAME = "TelegramPcControlBotProxy"
RUN_REGISTRY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPS_PER_PAGE = 8
WINDOWS_PER_PAGE = 8
LAUNCH_ITEMS_PER_PAGE = 8
CONFIG_FILE = "config.json"
LAUNCH_CATALOG_FILE = "launch_catalog.json"
LAUNCH_CATALOG_EXAMPLE_FILE = "launch_catalog.example.json"
LOG_FILE = "telegram_pc_bot.log"
LAUNCHER_FILE = "launch_hidden.vbs"
SESSION_FILE = "mtproto_bot"
SNAPSHOT_LIMIT = 20

SYSTEM_PROCESS_NAMES = {
    "nvidia overlay.exe",
    "searchhost.exe",
    "searchapp.exe",
    "shellexperiencehost.exe",
    "startmenuexperiencehost.exe",
    "textinputhost.exe",
    "ctfmon.exe",
    "sihost.exe",
    "widgets.exe",
    "lockapp.exe",
}

SYSTEM_WINDOW_CLASSES = {
    "progman",
    "workerw",
    "shell_traywnd",
    "msctfime ui",
    "windows.ui.core.corewindow",
    "applicationframeinputsinkwindow",
}

SYSTEM_TITLE_FRAGMENTS = (
    "default ime",
    "windows input experience",
    "desktopwindowxamlsource",
    "microsoft text input application",
    "интерфейс ввода windows",
)

FRIENDLY_APP_NAMES = {
    "ayugram": "AyuGram",
    "browser": "Яндекс Браузер",
    "chrome": "Google Chrome",
    "discord": "Discord",
    "explorer": "Проводник",
    "firefox": "Firefox",
    "flclash": "FL Clash",
    "luxifyassistant": "Lux Assistant",
    "msedge": "Microsoft Edge",
    "notepad": "Блокнот",
    "opera": "Opera",
    "telegram": "Telegram",
    "code": "VS Code",
}


class ConfigError(Exception):
    """Raised when the bot configuration is missing or invalid."""


class LaunchCatalogError(Exception):
    """Raised when the launch catalog is missing or invalid."""


@dataclass(slots=True)
class AppConfig:
    telegram_token: str
    allowed_user_id: int
    telegram_api_id: int
    telegram_api_hash: str
    mtproto_server: str
    mtproto_port: int
    mtproto_secret: str

    @property
    def mtproto_proxy(self) -> tuple[str, int, str]:
        return (self.mtproto_server, self.mtproto_port, self.mtproto_secret)


@dataclass(slots=True)
class OpenWindow:
    hwnd: int
    pid: int
    process_name: str
    app_name: str
    window_title: str
    group_key: str

    @property
    def display_name(self) -> str:
        return f"{self.app_name} - {self.window_title}"


@dataclass(slots=True)
class AppGroup:
    group_key: str
    app_name: str
    windows: list[OpenWindow]


@dataclass(slots=True)
class LaunchApp:
    app_id: str
    title: str
    target: str
    arguments: str
    start_in: str
    window_style: str


@dataclass(slots=True)
class LaunchPack:
    pack_id: str
    title: str
    apps: list[str]
    delay_ms: int


@dataclass(slots=True)
class LaunchCatalog:
    apps: list[LaunchApp]
    packs: list[LaunchPack]


@dataclass(slots=True)
class LaunchMenuItem:
    kind: str
    item_id: str
    title: str


class BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", ctypes.c_ushort),
        ("biBitCount", ctypes.c_ushort),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class RgbQuad(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class BitmapInfo(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BitmapInfoHeader),
        ("bmiColors", RgbQuad * 1),
    ]


class WindowsDesktop:
    USER32 = ctypes.windll.user32
    GDI32 = ctypes.windll.gdi32
    KERNEL32 = ctypes.windll.kernel32
    POWRPROF = ctypes.windll.powrprof
    DWMAPI = getattr(ctypes.windll, "dwmapi", None)
    WM_CLOSE = 0x0010
    WM_SYSCOMMAND = 0x0112
    SC_CLOSE = 0xF060
    GW_OWNER = 4
    GWL_EXSTYLE = -20
    SW_RESTORE = 9
    SW_SHOW = 5
    SW_MINIMIZE = 6
    WS_EX_APPWINDOW = 0x00040000
    WS_EX_TOOLWINDOW = 0x00000080
    DWMWA_CLOAKED = 14
    PW_RENDERFULLCONTENT = 0x00000002
    BI_RGB = 0
    DIB_RGB_COLORS = 0
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_SHOWWINDOW = 0x0040
    KEYEVENTF_KEYUP = 0x0002
    VK_MENU = 0x12
    ASFW_ANY = -1
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    @classmethod
    def list_open_windows(cls) -> list[OpenWindow]:
        windows: list[OpenWindow] = []

        def callback(hwnd: int, _: int) -> bool:
            window = cls._build_open_window(hwnd)
            if window is not None:
                windows.append(window)
            return True

        cls.USER32.EnumWindows(cls.WNDENUMPROC(callback), 0)
        return sorted(windows, key=lambda item: (item.app_name.lower(), item.window_title.lower()))

    @classmethod
    def list_app_groups(cls) -> list[AppGroup]:
        grouped: dict[str, AppGroup] = {}
        for window in cls.list_open_windows():
            group = grouped.get(window.group_key)
            if group is None:
                grouped[window.group_key] = AppGroup(
                    group_key=window.group_key,
                    app_name=window.app_name,
                    windows=[window],
                )
                continue
            group.windows.append(window)
        return sorted(grouped.values(), key=lambda item: (item.app_name.lower(), item.windows[0].window_title.lower()))

    @classmethod
    def close_window(cls, hwnd: int) -> str:
        if not cls.USER32.IsWindow(hwnd):
            return "Окно уже закрыто."

        title = cls._get_window_text(hwnd)
        close_sent = cls.USER32.PostMessageW(hwnd, cls.WM_SYSCOMMAND, cls.SC_CLOSE, 0)
        if close_sent == 0:
            close_sent = cls.USER32.PostMessageW(hwnd, cls.WM_CLOSE, 0, 0)
        if close_sent == 0:
            return "Не удалось отправить мягкую команду на закрытие."

        for _ in range(30):
            if not cls.USER32.IsWindow(hwnd):
                if title:
                    return f"Окно «{title}» закрыто."
                return "Окно закрыто."
            time.sleep(0.1)

        if title:
            return (
                f"Команда на мягкое закрытие для окна «{title}» отправлена, "
                "но окно все еще открыто. Возможно, приложение просит сохранить данные."
            )
        return "Команда на мягкое закрытие отправлена, но окно пока не закрылось."

    @classmethod
    def capture_window_png(cls, hwnd: int) -> bytes | None:
        if not cls.USER32.IsWindow(hwnd):
            return None

        image = cls._capture_window_with_printwindow(hwnd)
        if image is None:
            image = cls._capture_window_from_screen(hwnd)
        if image is None:
            return None

        image.thumbnail((1600, 1600))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    @classmethod
    def capture_preview_png(cls, hwnd: int) -> bytes | None:
        if not cls.USER32.IsWindow(hwnd):
            return None

        previous_hwnd = cls.USER32.GetForegroundWindow()
        was_minimized = bool(cls.USER32.IsIconic(hwnd))
        try:
            cls.focus_window(hwnd)

            image = None
            for _ in range(4):
                time.sleep(0.18)
                if not cls._is_foreground_window(hwnd):
                    continue
                image = cls._capture_window_from_screen(hwnd)
                if image is not None:
                    break

            if image is None:
                image = cls._capture_window_with_printwindow(hwnd)
            if image is None:
                return None

            image.thumbnail((1600, 1600))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue()
        finally:
            if was_minimized and cls.USER32.IsWindow(hwnd):
                cls.USER32.ShowWindow(hwnd, cls.SW_MINIMIZE)
                time.sleep(0.05)
            if previous_hwnd and previous_hwnd != hwnd and cls.USER32.IsWindow(previous_hwnd):
                cls.focus_window(previous_hwnd)

    @classmethod
    def focus_window(cls, hwnd: int) -> bool:
        if not cls.USER32.IsWindow(hwnd):
            return False

        target_process = ctypes.c_ulong()
        foreground_process = ctypes.c_ulong()
        foreground_hwnd = cls.USER32.GetForegroundWindow()
        current_thread_id = cls.KERNEL32.GetCurrentThreadId()
        target_thread_id = cls.USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(target_process))
        foreground_thread_id = 0
        if foreground_hwnd:
            foreground_thread_id = cls.USER32.GetWindowThreadProcessId(
                foreground_hwnd,
                ctypes.byref(foreground_process),
            )

        attached_pairs: list[tuple[int, int]] = []

        def attach_thread_input(left_id: int, right_id: int) -> None:
            if not left_id or not right_id or left_id == right_id:
                return
            if cls.USER32.AttachThreadInput(left_id, right_id, True):
                attached_pairs.append((left_id, right_id))

        try:
            cls.USER32.AllowSetForegroundWindow(cls.ASFW_ANY)
            attach_thread_input(current_thread_id, foreground_thread_id)
            attach_thread_input(current_thread_id, target_thread_id)
            attach_thread_input(target_thread_id, foreground_thread_id)

            if cls.USER32.IsIconic(hwnd):
                cls.USER32.ShowWindow(hwnd, cls.SW_RESTORE)
            else:
                cls.USER32.ShowWindow(hwnd, cls.SW_SHOW)
            cls.USER32.SetWindowPos(
                hwnd,
                cls.HWND_TOPMOST,
                0,
                0,
                0,
                0,
                cls.SWP_NOMOVE | cls.SWP_NOSIZE | cls.SWP_SHOWWINDOW,
            )
            cls.USER32.SetWindowPos(
                hwnd,
                cls.HWND_NOTOPMOST,
                0,
                0,
                0,
                0,
                cls.SWP_NOMOVE | cls.SWP_NOSIZE | cls.SWP_SHOWWINDOW,
            )
            cls.USER32.BringWindowToTop(hwnd)
            cls.USER32.SetActiveWindow(hwnd)
            cls.USER32.SetFocus(hwnd)
            cls.USER32.SetForegroundWindow(hwnd)

            if cls.USER32.GetForegroundWindow() != hwnd:
                cls.USER32.keybd_event(cls.VK_MENU, 0, 0, 0)
                cls.USER32.keybd_event(cls.VK_MENU, 0, cls.KEYEVENTF_KEYUP, 0)
                cls.USER32.SetForegroundWindow(hwnd)
                cls.USER32.BringWindowToTop(hwnd)

            return cls._is_foreground_window(hwnd)
        except Exception:  # noqa: BLE001
            return False
        finally:
            for left_id, right_id in reversed(attached_pairs):
                try:
                    cls.USER32.AttachThreadInput(left_id, right_id, False)
                except Exception:  # noqa: BLE001
                    pass

    @classmethod
    def sleep_computer(cls) -> None:
        cls.POWRPROF.SetSuspendState.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ubyte]
        cls.POWRPROF.SetSuspendState.restype = ctypes.c_ubyte
        result = cls.POWRPROF.SetSuspendState(0, 0, 0)
        if result == 0:
            raise ctypes.WinError()

    @classmethod
    def minimize_all_windows(cls) -> None:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "(New-Object -ComObject Shell.Application).MinimizeAll()",
            ],
            check=True,
            creationflags=creation_flags,
        )

    @classmethod
    def _build_open_window(cls, hwnd: int) -> OpenWindow | None:
        if not cls.USER32.IsWindowVisible(hwnd):
            return None
        if cls._is_window_cloaked(hwnd):
            return None

        ex_style = cls.USER32.GetWindowLongW(hwnd, cls.GWL_EXSTYLE)
        if ex_style & cls.WS_EX_TOOLWINDOW:
            return None

        owner = cls.USER32.GetWindow(hwnd, cls.GW_OWNER)
        if owner and not (ex_style & cls.WS_EX_APPWINDOW):
            return None

        rect = cls.RECT()
        if cls.USER32.GetWindowRect(hwnd, ctypes.byref(rect)):
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            if width <= 1 or height <= 1:
                return None

        title = cls._get_window_text(hwnd)
        if not title or title == "Program Manager":
            return None

        lowered_title = title.casefold()
        if any(fragment in lowered_title for fragment in SYSTEM_TITLE_FRAGMENTS):
            return None

        class_name = cls._get_class_name(hwnd)
        if class_name.casefold() in SYSTEM_WINDOW_CLASSES:
            return None

        pid = ctypes.c_ulong()
        cls.USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == 0 or pid.value == os.getpid():
            return None

        try:
            process = psutil.Process(pid.value)
            process_name = process.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

        if process_name.casefold() in SYSTEM_PROCESS_NAMES:
            return None

        group_key = cls._build_group_key(process_name, process)
        app_name = cls._friendly_app_name(process_name)
        return OpenWindow(
            hwnd=hwnd,
            pid=pid.value,
            process_name=process_name,
            app_name=app_name,
            window_title=title,
            group_key=group_key,
        )

    @classmethod
    def _capture_window_with_printwindow(cls, hwnd: int) -> Image.Image | None:
        rect = cls.RECT()
        if not cls.USER32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None

        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None

        window_dc = cls.USER32.GetWindowDC(hwnd)
        if not window_dc:
            return None

        memory_dc = cls.GDI32.CreateCompatibleDC(window_dc)
        if not memory_dc:
            cls.USER32.ReleaseDC(hwnd, window_dc)
            return None

        bitmap = cls.GDI32.CreateCompatibleBitmap(window_dc, width, height)
        if not bitmap:
            cls.GDI32.DeleteDC(memory_dc)
            cls.USER32.ReleaseDC(hwnd, window_dc)
            return None

        old_bitmap = cls.GDI32.SelectObject(memory_dc, bitmap)
        try:
            result = cls.USER32.PrintWindow(hwnd, memory_dc, cls.PW_RENDERFULLCONTENT)
            if result != 1:
                result = cls.USER32.PrintWindow(hwnd, memory_dc, 0)
            if result != 1:
                return None

            bitmap_info = BitmapInfo()
            bitmap_info.bmiHeader.biSize = ctypes.sizeof(BitmapInfoHeader)
            bitmap_info.bmiHeader.biWidth = width
            bitmap_info.bmiHeader.biHeight = -height
            bitmap_info.bmiHeader.biPlanes = 1
            bitmap_info.bmiHeader.biBitCount = 32
            bitmap_info.bmiHeader.biCompression = cls.BI_RGB

            buffer_size = width * height * 4
            pixel_buffer = (ctypes.c_ubyte * buffer_size)()
            scan_lines = cls.GDI32.GetDIBits(
                memory_dc,
                bitmap,
                0,
                height,
                pixel_buffer,
                ctypes.byref(bitmap_info),
                cls.DIB_RGB_COLORS,
            )
            if scan_lines != height:
                return None

            return Image.frombuffer(
                "RGBA",
                (width, height),
                pixel_buffer,
                "raw",
                "BGRA",
                0,
                1,
            ).copy()
        finally:
            if old_bitmap:
                cls.GDI32.SelectObject(memory_dc, old_bitmap)
            cls.GDI32.DeleteObject(bitmap)
            cls.GDI32.DeleteDC(memory_dc)
            cls.USER32.ReleaseDC(hwnd, window_dc)

    @classmethod
    def _capture_window_from_screen(cls, hwnd: int) -> Image.Image | None:
        rect = cls.RECT()
        if not cls.USER32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None

        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None

        try:
            return ImageGrab.grab(
                bbox=(rect.left, rect.top, rect.right, rect.bottom),
                all_screens=True,
            )
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def _is_foreground_window(cls, hwnd: int) -> bool:
        return cls.USER32.GetForegroundWindow() == hwnd

    @classmethod
    def _build_group_key(cls, process_name: str, process: psutil.Process) -> str:
        try:
            executable = process.exe()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            executable = ""
        if executable:
            return executable.casefold()
        return process_name.casefold()

    @classmethod
    def _friendly_app_name(cls, process_name: str) -> str:
        stem = Path(process_name).stem
        return FRIENDLY_APP_NAMES.get(stem.casefold(), stem.replace("_", " ").replace("-", " ").title())

    @classmethod
    def _get_window_text(cls, hwnd: int) -> str:
        title_length = cls.USER32.GetWindowTextLengthW(hwnd)
        if title_length <= 0:
            return ""
        title_buffer = ctypes.create_unicode_buffer(title_length + 1)
        cls.USER32.GetWindowTextW(hwnd, title_buffer, title_length + 1)
        return title_buffer.value.strip()

    @classmethod
    def _get_class_name(cls, hwnd: int) -> str:
        class_buffer = ctypes.create_unicode_buffer(256)
        cls.USER32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
        return class_buffer.value.strip()

    @classmethod
    def _is_window_cloaked(cls, hwnd: int) -> bool:
        if cls.DWMAPI is None:
            return False
        cloaked = ctypes.c_int()
        result = cls.DWMAPI.DwmGetWindowAttribute(
            hwnd,
            cls.DWMWA_CLOAKED,
            ctypes.byref(cloaked),
            ctypes.sizeof(cloaked),
        )
        return result == 0 and cloaked.value != 0


class TelegramPcControlApp:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.config_path = root_dir / CONFIG_FILE
        self.launch_catalog_path = root_dir / LAUNCH_CATALOG_FILE
        self.log_path = root_dir / LOG_FILE

        self.stop_event = threading.Event()
        self.restart_bot_event = threading.Event()
        self.bot_thread: threading.Thread | None = None
        self.bot_loop: asyncio.AbstractEventLoop | None = None
        self.client: TelegramClient | None = None
        self.current_config: AppConfig | None = None
        self.icon: pystray.Icon | None = None
        self.started_at = time.time()
        self.bot_status = "Запускается"
        self.last_config_error: str | None = None
        self.status_lock = threading.Lock()
        self.snapshot_lock = threading.Lock()
        self.snapshot_serial = 0
        self.apps_snapshots: dict[str, list[AppGroup]] = {}
        self.preview_lock = threading.Lock()
        self.preview_messages: dict[int, int] = {}

    def run(self) -> None:
        self._setup_logging()
        self._write_default_launch_catalog()
        self._ensure_autostart()
        self._start_bot_thread()
        self._run_tray_icon()

    def stop(self) -> None:
        self.stop_event.set()
        self.restart_bot_event.set()
        if self.icon is not None:
            self.icon.stop()
        if self.bot_thread is not None and self.bot_thread.is_alive():
            self.bot_thread.join(timeout=10)

    def _setup_logging(self) -> None:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.FileHandler(self.log_path, encoding="utf-8"),
            ],
        )

    def _ensure_autostart(self) -> None:
        self._write_launcher_script()
        command = self._build_launch_command()
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REGISTRY_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
                current_value = ""
                try:
                    current_value, _ = winreg.QueryValueEx(key, AUTOSTART_KEY_NAME)
                except FileNotFoundError:
                    pass
                if current_value != command:
                    winreg.SetValueEx(key, AUTOSTART_KEY_NAME, 0, winreg.REG_SZ, command)
                    logging.info("Автозапуск обновлен: %s", command)
        except OSError as error:
            logging.exception("Не удалось настроить автозапуск: %s", error)
            self._set_bot_status("Ошибка автозапуска")

    def _build_launch_command(self) -> str:
        if getattr(sys, "frozen", False):
            return subprocess.list2cmdline([str(Path(sys.executable).resolve())])

        launcher_path = self.root_dir / LAUNCHER_FILE
        return subprocess.list2cmdline(["wscript.exe", str(launcher_path)])

    def _start_bot_thread(self) -> None:
        self.bot_thread = threading.Thread(target=self._bot_worker, name="telegram-bot-worker", daemon=True)
        self.bot_thread.start()

    def _bot_worker(self) -> None:
        while not self.stop_event.is_set():
            self.restart_bot_event.clear()

            try:
                config = self._load_config()
            except ConfigError as error:
                self._set_bot_status("Нужно заполнить config.json")
                error_text = str(error)
                if error_text != self.last_config_error:
                    logging.warning(error_text)
                    self.last_config_error = error_text
                self._wait_with_restart_support(5)
                continue
            self.last_config_error = None

            try:
                asyncio.run(self._run_bot_session(config))
            except Exception as error:  # noqa: BLE001
                logging.exception("Бот остановился с ошибкой: %s", error)
                self._set_bot_status("Ошибка подключения")
                self._wait_with_restart_support(10)

    async def _run_bot_session(self, config: AppConfig) -> None:
        self.bot_loop = asyncio.get_running_loop()
        self.current_config = config
        session_path = str(self.root_dir / SESSION_FILE)
        self.client = TelegramClient(
            session_path,
            config.telegram_api_id,
            config.telegram_api_hash,
            connection=connection.ConnectionTcpMTProxyRandomizedIntermediate,
            proxy=config.mtproto_proxy,
        )
        self.client.add_event_handler(self._handle_new_message, events.NewMessage(incoming=True))
        self.client.add_event_handler(self._handle_callback, events.CallbackQuery())

        logging.info(
            "Используется MTProto proxy %s:%s",
            config.mtproto_server,
            config.mtproto_port,
        )
        await self.client.start(bot_token=config.telegram_token)
        self._set_bot_status("Онлайн")
        logging.info("MTProto Telegram-бот запущен.")

        try:
            while not self.stop_event.is_set() and not self.restart_bot_event.is_set():
                await asyncio.sleep(0.5)
        finally:
            self._set_bot_status("Перезапуск")
            if self.client is not None:
                await self.client.disconnect()
            self.client = None
            self.current_config = None
            self.bot_loop = None
            logging.info("MTProto Telegram-бот остановлен.")

    def _wait_with_restart_support(self, seconds: int) -> None:
        end_at = time.time() + seconds
        while time.time() < end_at:
            if self.stop_event.is_set() or self.restart_bot_event.is_set():
                return
            time.sleep(0.2)

    def _run_tray_icon(self) -> None:
        image = self._create_tray_image()
        menu = pystray.Menu(
            pystray.MenuItem(lambda _: f"Состояние: {self.bot_status}", None, enabled=False),
            pystray.MenuItem("Открыть config.json", self._on_open_config),
            pystray.MenuItem("Редактор запусков", self._on_open_launch_editor),
            pystray.MenuItem(f"Открыть {LAUNCH_CATALOG_FILE}", self._on_open_launch_catalog),
            pystray.MenuItem("Перезапустить бота", self._on_restart_bot),
            pystray.MenuItem("Выход", self._on_exit),
        )
        self.icon = pystray.Icon(APP_NAME, image, APP_NAME, menu)
        self.icon.run()

    def _create_tray_image(self) -> Image.Image:
        image = Image.new("RGBA", (64, 64), (35, 46, 63, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((10, 10, 54, 44), radius=8, fill=(67, 115, 212, 255))
        draw.rectangle((18, 18, 46, 34), fill=(245, 249, 255, 255))
        draw.rectangle((26, 46, 38, 52), fill=(67, 115, 212, 255))
        draw.rectangle((22, 52, 42, 56), fill=(174, 196, 241, 255))
        draw.polygon([(44, 14), (54, 20), (44, 26), (46, 21)], fill=(63, 194, 251, 255))
        return image

    def _on_open_config(self, _: pystray.Icon, __: pystray.MenuItem) -> None:
        if not self.config_path.exists():
            self._write_default_config()
        subprocess.Popen(["notepad.exe", str(self.config_path)])

    def _on_open_launch_editor(self, _: pystray.Icon, __: pystray.MenuItem) -> None:
        self._write_default_launch_catalog()
        editor_path = self.root_dir / "launch_catalog_editor.py"
        pythonw_path = Path(sys.executable).with_name("pythonw.exe")
        python_path = pythonw_path if pythonw_path.exists() else Path(sys.executable)
        subprocess.Popen([str(python_path), str(editor_path)])

    def _on_open_launch_catalog(self, _: pystray.Icon, __: pystray.MenuItem) -> None:
        self._write_default_launch_catalog()
        subprocess.Popen(["notepad.exe", str(self.launch_catalog_path)])

    def _on_restart_bot(self, _: pystray.Icon, __: pystray.MenuItem) -> None:
        self._set_bot_status("Перезапуск")
        self.restart_bot_event.set()

    def _on_exit(self, icon: pystray.Icon, _: pystray.MenuItem) -> None:
        self.stop_event.set()
        self.restart_bot_event.set()
        icon.stop()

    def _set_bot_status(self, status: str) -> None:
        with self.status_lock:
            self.bot_status = status
        if self.icon is not None:
            self.icon.title = f"{APP_NAME}\n{status}"

    def _write_default_config(self) -> None:
        if self.config_path.exists():
            return
        example = {
            "telegram_token": "",
            "allowed_user_id": 0,
            "telegram_api_id": 0,
            "telegram_api_hash": "",
            "telegram_proxy": "",
        }
        self.config_path.write_text(json.dumps(example, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_default_launch_catalog(self) -> None:
        if self.launch_catalog_path.exists():
            return

        example_path = self.root_dir / LAUNCH_CATALOG_EXAMPLE_FILE
        if example_path.exists():
            self.launch_catalog_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
            return

        sample = {"apps": [], "packs": []}
        self.launch_catalog_path.write_text(json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_launcher_script(self) -> None:
        launcher_path = self.root_dir / LAUNCHER_FILE
        content = (
            'Set shell = CreateObject("WScript.Shell")\r\n'
            'Set fso = CreateObject("Scripting.FileSystemObject")\r\n'
            'projectRoot = fso.GetParentFolderName(WScript.ScriptFullName)\r\n'
            'pythonExe = projectRoot & "\\.venv\\Scripts\\python.exe"\r\n'
            'scriptPath = projectRoot & "\\telegram_pc_bot.py"\r\n'
            'shell.Run Chr(34) & pythonExe & Chr(34) & " " & Chr(34) & scriptPath & Chr(34), 0, False\r\n'
        )
        if launcher_path.exists() and launcher_path.read_text(encoding="utf-8") == content:
            return
        launcher_path.write_text(content, encoding="utf-8")

    def _load_config(self) -> AppConfig:
        if not self.config_path.exists():
            self._write_default_config()
            raise ConfigError(
                "Файл config.json создан. Заполните telegram_token, allowed_user_id, "
                "telegram_api_id, telegram_api_hash и telegram_proxy."
            )

        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ConfigError(f"config.json поврежден: {error}") from error

        token = str(raw.get("telegram_token", "")).strip()
        allowed_user_id = int(raw.get("allowed_user_id", 0))
        telegram_api_id = int(raw.get("telegram_api_id", 0))
        telegram_api_hash = str(raw.get("telegram_api_hash", "")).strip()
        mtproto_server, mtproto_port, mtproto_secret = self._parse_mtproto_proxy(
            raw.get("telegram_proxy", raw.get("mtproto_proxy_link", ""))
        )

        if not token:
            raise ConfigError("В config.json не заполнен telegram_token.")
        if allowed_user_id <= 0:
            raise ConfigError("В config.json не заполнен allowed_user_id.")
        if telegram_api_id <= 0:
            raise ConfigError("В config.json не заполнен telegram_api_id.")
        if not telegram_api_hash:
            raise ConfigError("В config.json не заполнен telegram_api_hash.")

        return AppConfig(
            telegram_token=token,
            allowed_user_id=allowed_user_id,
            telegram_api_id=telegram_api_id,
            telegram_api_hash=telegram_api_hash,
            mtproto_server=mtproto_server,
            mtproto_port=mtproto_port,
            mtproto_secret=mtproto_secret,
        )

    def _load_launch_catalog(self) -> LaunchCatalog:
        if not self.launch_catalog_path.exists():
            self._write_default_launch_catalog()

        try:
            raw = json.loads(self.launch_catalog_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise LaunchCatalogError(f"{LAUNCH_CATALOG_FILE} поврежден: {error}") from error

        raw_apps = raw.get("apps", [])
        raw_packs = raw.get("packs", [])
        if not isinstance(raw_apps, list):
            raise LaunchCatalogError(f"В {LAUNCH_CATALOG_FILE} поле apps должно быть списком.")
        if not isinstance(raw_packs, list):
            raise LaunchCatalogError(f"В {LAUNCH_CATALOG_FILE} поле packs должно быть списком.")

        apps: list[LaunchApp] = []
        seen_app_ids: set[str] = set()
        for index, item in enumerate(raw_apps, start=1):
            if not isinstance(item, dict):
                raise LaunchCatalogError(f"Элемент apps #{index} должен быть объектом.")

            app_id = str(item.get("id", "")).strip()
            title = str(item.get("title", "")).strip()
            target = str(item.get("target", "")).strip()
            arguments = str(item.get("arguments", item.get("args", ""))).strip()
            start_in = str(item.get("start_in", item.get("cwd", ""))).strip()
            window_style = str(item.get("window_style", "normal")).strip().casefold()

            if not app_id:
                raise LaunchCatalogError(f"В apps #{index} не заполнен id.")
            if ":" in app_id:
                raise LaunchCatalogError(f"В apps #{index} id не должен содержать двоеточие.")
            if app_id in seen_app_ids:
                raise LaunchCatalogError(f"Дублируется id приложения: {app_id}")
            if not title:
                raise LaunchCatalogError(f"В apps #{index} не заполнен title.")
            if not target:
                raise LaunchCatalogError(f"В apps #{index} не заполнен target.")
            if window_style not in {"normal", "minimized", "maximized"}:
                raise LaunchCatalogError(
                    f"В apps #{index} window_style должен быть normal, minimized или maximized."
                )

            apps.append(
                LaunchApp(
                    app_id=app_id,
                    title=title,
                    target=target,
                    arguments=arguments,
                    start_in=start_in,
                    window_style=window_style,
                )
            )
            seen_app_ids.add(app_id)

        packs: list[LaunchPack] = []
        seen_pack_ids: set[str] = set()
        for index, item in enumerate(raw_packs, start=1):
            if not isinstance(item, dict):
                raise LaunchCatalogError(f"Элемент packs #{index} должен быть объектом.")

            pack_id = str(item.get("id", "")).strip()
            title = str(item.get("title", "")).strip()
            raw_pack_apps = item.get("apps", [])

            try:
                delay_ms = int(item.get("delay_ms", 700))
            except (TypeError, ValueError) as error:
                raise LaunchCatalogError(f"В packs #{index} delay_ms должен быть числом.") from error

            if not pack_id:
                raise LaunchCatalogError(f"В packs #{index} не заполнен id.")
            if ":" in pack_id:
                raise LaunchCatalogError(f"В packs #{index} id не должен содержать двоеточие.")
            if pack_id in seen_pack_ids:
                raise LaunchCatalogError(f"Дублируется id пака: {pack_id}")
            if not title:
                raise LaunchCatalogError(f"В packs #{index} не заполнен title.")
            if not isinstance(raw_pack_apps, list):
                raise LaunchCatalogError(f"В packs #{index} поле apps должно быть списком id.")
            if delay_ms < 0:
                raise LaunchCatalogError(f"В packs #{index} delay_ms не может быть отрицательным.")

            pack_apps = [str(value).strip() for value in raw_pack_apps if str(value).strip()]
            if not pack_apps:
                raise LaunchCatalogError(f"В packs #{index} нет ни одного приложения.")

            packs.append(
                LaunchPack(
                    pack_id=pack_id,
                    title=title,
                    apps=pack_apps,
                    delay_ms=delay_ms,
                )
            )
            seen_pack_ids.add(pack_id)

        known_apps = {app.app_id for app in apps}
        for pack in packs:
            missing = [app_id for app_id in pack.apps if app_id not in known_apps]
            if missing:
                raise LaunchCatalogError(
                    f"Пак {pack.title} ссылается на несуществующие apps id: {', '.join(missing)}"
                )

        return LaunchCatalog(apps=apps, packs=packs)

    @staticmethod
    def _parse_mtproto_proxy(raw_value: object) -> tuple[str, int, str]:
        proxy = str(raw_value or "").strip()
        if not proxy:
            raise ConfigError("В config.json не заполнен telegram_proxy с MTProto-ссылкой.")

        lowered = proxy.casefold()
        server = ""
        port_text = ""
        secret = ""

        if lowered.startswith("http://t.me/proxy?") or lowered.startswith("https://t.me/proxy?") or lowered.startswith("tg://proxy?"):
            parsed = urllib.parse.urlparse(proxy)
            query = urllib.parse.parse_qs(parsed.query)
            server = query.get("server", [""])[0].strip()
            port_text = query.get("port", [""])[0].strip()
            secret = query.get("secret", [""])[0].strip()
        else:
            parts = proxy.split(":")
            if len(parts) >= 3:
                server = parts[0].strip()
                port_text = parts[1].strip()
                secret = ":".join(parts[2:]).strip()

        if not server or not port_text or not secret:
            raise ConfigError(
                "telegram_proxy должен быть ссылкой вида t.me/proxy?server=...&port=...&secret=... "
                "или строкой host:port:secret."
            )

        try:
            port = int(port_text)
        except ValueError as error:
            raise ConfigError("Порт в telegram_proxy должен быть числом.") from error

        if port <= 0:
            raise ConfigError("Порт в telegram_proxy должен быть положительным.")

        return server, port, secret

    async def _handle_new_message(self, event) -> None:
        if not await self._ensure_authorized(event):
            return
        if not (event.raw_text or "").strip():
            return
        await self._send_main_menu(event)

    async def _handle_callback(self, event) -> None:
        if not await self._ensure_authorized(event):
            return
        if event.data is None:
            return

        await event.answer()
        message = await event.get_message()
        if message is None:
            return

        parts = event.data.decode("utf-8").split(":")
        action = parts[0]

        if action == "main":
            await self._show_main_menu(message)
            return

        if action == "refresh":
            await self._refresh_main_menu(message)
            return

        if action == "apps":
            page = int(parts[1]) if len(parts) > 1 else 0
            await self._show_apps_page(message, page)
            return

        if action == "launches":
            page = int(parts[1]) if len(parts) > 1 else 0
            await self._show_launches_page(message, page)
            return

        if action == "launchapp":
            app_id = parts[1]
            page = int(parts[2]) if len(parts) > 2 else 0
            await self._launch_single_catalog_app(message, app_id, page)
            return

        if action == "launchpack":
            pack_id = parts[1]
            page = int(parts[2]) if len(parts) > 2 else 0
            await self._launch_catalog_pack(message, pack_id, page)
            return

        if action == "desktop":
            await self._minimize_all_windows(message)
            return

        if action == "app":
            token = parts[1]
            group_index = int(parts[2])
            apps_page = int(parts[3]) if len(parts) > 3 else 0
            await self._open_app_group(message, token, group_index, apps_page)
            return

        if action == "wins":
            token = parts[1]
            group_index = int(parts[2])
            window_page = int(parts[3]) if len(parts) > 3 else 0
            apps_page = int(parts[4]) if len(parts) > 4 else 0
            await self._show_windows_page(message, token, group_index, window_page, apps_page)
            return

        if action == "win":
            token = parts[1]
            group_index = int(parts[2])
            window_index = int(parts[3])
            window_page = int(parts[4]) if len(parts) > 4 else 0
            apps_page = int(parts[5]) if len(parts) > 5 else 0
            await self._show_close_confirmation(message, token, group_index, window_index, window_page, apps_page)
            return

        if action == "preview":
            token = parts[1]
            group_index = int(parts[2])
            window_index = int(parts[3])
            window_page = int(parts[4]) if len(parts) > 4 else 0
            apps_page = int(parts[5]) if len(parts) > 5 else 0
            await self._show_window_preview(message, token, group_index, window_index, window_page, apps_page)
            return

        if action == "winclose":
            token = parts[1]
            group_index = int(parts[2])
            window_index = int(parts[3])
            window_page = int(parts[4]) if len(parts) > 4 else 0
            apps_page = int(parts[5]) if len(parts) > 5 else 0
            await self._close_selected_window(message, token, group_index, window_index, window_page, apps_page)
            return

        if action == "wincancel":
            token = parts[1]
            group_index = int(parts[2])
            window_page = int(parts[3]) if len(parts) > 3 else 0
            apps_page = int(parts[4]) if len(parts) > 4 else 0
            await self._cancel_window_close(message, token, group_index, window_page, apps_page)
            return

        if action == "power":
            sub_action = parts[1]
            power_action = parts[2]
            if sub_action == "confirm":
                await self._show_power_confirmation(message, power_action)
                return
            if sub_action == "run":
                await self._run_power_action(message, power_action)
                return

        if action == "cancel":
            if len(parts) >= 2 and parts[1] == "apps":
                page = int(parts[2]) if len(parts) > 2 else 0
                await self._show_apps_page(message, page)
                return
            await self._show_main_menu(message)

    async def _ensure_authorized(self, event) -> bool:
        config = self.current_config
        if config is None:
            return False

        sender_id = getattr(event, "sender_id", None)
        is_private = getattr(event, "is_private", False)
        if is_private and sender_id == config.allowed_user_id:
            return True

        if getattr(event, "data", None) is not None:
            await event.answer("Доступ запрещен.", alert=True)
        else:
            await event.respond("Доступ запрещен.")
        logging.warning("Отклонен доступ от пользователя id=%s", sender_id)
        return False

    async def _send_main_menu(self, event) -> None:
        await event.respond(
            self._build_status_text(),
            buttons=self._main_menu_keyboard(),
            parse_mode="html",
        )

    async def _show_main_menu(self, message: Message, notice: str | None = None) -> None:
        await self._delete_preview_message(message.chat_id)
        await self._safe_edit_message(
            message,
            self._build_screen_text(self._build_status_text(), notice),
            self._main_menu_keyboard(),
        )

    async def _refresh_main_menu(self, message: Message) -> None:
        await self._safe_edit_message(message, self._build_status_text(), self._main_menu_keyboard())

    async def _show_apps_page(self, message: Message, page: int, notice: str | None = None) -> None:
        await self._delete_preview_message(message.chat_id)
        apps = WindowsDesktop.list_app_groups()
        token = self._store_apps_snapshot(apps)
        total_pages = max((len(apps) - 1) // APPS_PER_PAGE + 1, 1)
        page = max(0, min(page, total_pages - 1))

        start_index = page * APPS_PER_PAGE
        page_apps = apps[start_index : start_index + APPS_PER_PAGE]

        notice_block = ""
        if notice:
            notice_block = f"{html.escape(notice)}\n\n"

        if not page_apps:
            text = (
                f"{notice_block}<b>Активные приложения</b>\n\n"
                "Сейчас не найдено открытых пользовательских окон."
            )
        else:
            lines = [
                f"{notice_block}<b>Активные приложения</b>",
                "",
            ]
            for offset, app in enumerate(page_apps, start=start_index + 1):
                lines.append(
                    f"{offset}. <b>{html.escape(app.app_name)}</b> — "
                    f"{self._format_window_count(len(app.windows))}"
                )
            text = "\n".join(lines)

        await self._safe_edit_message(message, text, self._apps_keyboard(token, page, total_pages, page_apps, start_index))

    async def _show_launches_page(self, message: Message, page: int, notice: str | None = None) -> None:
        await self._delete_preview_message(message.chat_id)
        try:
            catalog = self._load_launch_catalog()
        except LaunchCatalogError as error:
            text = self._build_screen_text(
                f"<b>Запуск приложений</b>\n\n{html.escape(str(error))}",
                notice,
            )
            await self._safe_edit_message(message, text, self._launches_back_keyboard())
            return

        items = self._build_launch_menu_items(catalog)
        total_pages = max((len(items) - 1) // LAUNCH_ITEMS_PER_PAGE + 1, 1)
        page = max(0, min(page, total_pages - 1))
        start_index = page * LAUNCH_ITEMS_PER_PAGE
        page_items = items[start_index : start_index + LAUNCH_ITEMS_PER_PAGE]

        lines = ["<b>Запуск приложений</b>", ""]
        if not page_items:
            lines.append(f"Список пока пуст. Отредактируйте {LAUNCH_CATALOG_FILE}.")
        else:
            for offset, item in enumerate(page_items, start=start_index + 1):
                prefix = "📦" if item.kind == "pack" else "▶️"
                lines.append(f"{offset}. {prefix} {html.escape(item.title)}")
            lines.append("")
            lines.append(f"Новые кнопки добавляются через {LAUNCH_CATALOG_FILE}.")

        text = self._build_screen_text("\n".join(lines), notice)
        await self._safe_edit_message(message, text, self._launches_keyboard(page_items, page, total_pages))

    async def _open_app_group(self, message: Message, token: str, group_index: int, apps_page: int) -> None:
        group = self._get_snapshot_group(token, group_index)
        if group is None:
            await self._show_apps_page(message, apps_page, notice="Список приложений изменился, поэтому я его обновил.")
            return
        if len(group.windows) == 1:
            await self._show_close_confirmation(message, token, group_index, 0, 0, apps_page)
            return
        await self._show_windows_page(message, token, group_index, 0, apps_page)

    async def _show_windows_page(
        self,
        message: Message,
        token: str,
        group_index: int,
        window_page: int,
        apps_page: int,
        notice: str | None = None,
    ) -> None:
        await self._delete_preview_message(message.chat_id)
        group = self._get_snapshot_group(token, group_index)
        if group is None:
            await self._show_apps_page(message, apps_page, notice="Список приложений изменился, поэтому я его обновил.")
            return

        live_groups = WindowsDesktop.list_app_groups()
        live_token = self._store_apps_snapshot(live_groups)
        live_group_index = self._find_group_index_by_key(live_groups, group.group_key)
        if live_group_index is None:
            await self._show_apps_page(message, apps_page, notice=notice or "Это приложение уже не найдено.")
            return

        live_group = live_groups[live_group_index]
        total_pages = max((len(live_group.windows) - 1) // WINDOWS_PER_PAGE + 1, 1)
        window_page = max(0, min(window_page, total_pages - 1))
        start_index = window_page * WINDOWS_PER_PAGE
        page_windows = live_group.windows[start_index : start_index + WINDOWS_PER_PAGE]

        lines = []
        if notice:
            lines.append(html.escape(notice))
            lines.append("")
        lines.extend(
            [
                f"<b>{html.escape(live_group.app_name)}</b>",
                f"Открытых окон: {self._format_window_count(len(live_group.windows))}",
                "",
            ]
        )
        for offset, window in enumerate(page_windows, start=start_index + 1):
            lines.append(f"{offset}. {html.escape(window.window_title)}")
        text = "\n".join(lines)
        await self._safe_edit_message(
            message,
            text,
            self._windows_keyboard(live_token, live_group_index, window_page, total_pages, page_windows, start_index, apps_page),
        )

    async def _show_close_confirmation(
        self,
        message: Message,
        token: str,
        group_index: int,
        window_index: int,
        window_page: int,
        apps_page: int,
    ) -> None:
        window = self._get_snapshot_window(token, group_index, window_index)
        if window is None:
            await self._show_apps_page(message, apps_page, notice="Окно уже не найдено.")
            return

        text = (
            "<b>Закрыть окно?</b>\n\n"
            f"<b>Приложение:</b> {html.escape(window.app_name)}\n"
            f"<b>Окно:</b> {html.escape(window.window_title)}"
        )
        keyboard = [
            [
                Button.inline(
                    "Показать экран",
                    data=f"preview:{token}:{group_index}:{window_index}:{window_page}:{apps_page}",
                )
            ],
            [
                Button.inline("Да", data=f"winclose:{token}:{group_index}:{window_index}:{window_page}:{apps_page}"),
                Button.inline("Нет", data=f"wincancel:{token}:{group_index}:{window_page}:{apps_page}"),
            ]
        ]
        await self._safe_edit_message(message, text, keyboard)

    async def _show_window_preview(
        self,
        message: Message,
        token: str,
        group_index: int,
        window_index: int,
        window_page: int,
        apps_page: int,
    ) -> None:
        window = self._get_snapshot_window(token, group_index, window_index)
        if window is None or self.client is None:
            await self._show_apps_page(message, apps_page, notice="Окно уже не найдено.")
            return

        png_bytes = WindowsDesktop.capture_preview_png(window.hwnd)
        if not png_bytes:
            await self._show_close_confirmation(
                message,
                token,
                group_index,
                window_index,
                window_page,
                apps_page,
            )
            await self.client.send_message(message.chat_id, "Не удалось получить скриншот этого окна.")
            return

        await self._delete_preview_message(message.chat_id)
        image_stream = io.BytesIO(png_bytes)
        image_stream.name = "window_preview.png"
        preview_message = await self.client.send_file(
            message.chat_id,
            image_stream,
            reply_to=message.id,
        )
        self._remember_preview_message(message.chat_id, preview_message.id)
        await self._show_close_confirmation(
            message,
            token,
            group_index,
            window_index,
            window_page,
            apps_page,
        )

    async def _close_selected_window(
        self,
        message: Message,
        token: str,
        group_index: int,
        window_index: int,
        window_page: int,
        apps_page: int,
    ) -> None:
        await self._delete_preview_message(message.chat_id)
        group = self._get_snapshot_group(token, group_index)
        window = self._get_snapshot_window(token, group_index, window_index)
        if group is None or window is None:
            await self._show_apps_page(message, apps_page, notice="Окно уже не найдено.")
            return

        result = WindowsDesktop.close_window(window.hwnd)
        await self._show_windows_page(message, token, group_index, window_page, apps_page, notice=result)

    async def _cancel_window_close(
        self,
        message: Message,
        token: str,
        group_index: int,
        window_page: int,
        apps_page: int,
    ) -> None:
        await self._delete_preview_message(message.chat_id)
        group = self._get_snapshot_group(token, group_index)
        if group is None:
            await self._show_apps_page(message, apps_page, notice="Список приложений изменился, поэтому я его обновил.")
            return
        if len(group.windows) <= 1:
            await self._show_apps_page(message, apps_page)
            return
        await self._show_windows_page(message, token, group_index, window_page, apps_page)

    async def _show_power_confirmation(self, message: Message, power_action: str) -> None:
        await self._delete_preview_message(message.chat_id)
        titles = {
            "shutdown": "Выключить компьютер",
            "restart": "Перезагрузить компьютер",
            "sleep": "Перевести компьютер в спящий режим",
        }
        text = f"{titles[power_action]}?"
        keyboard = [
            [
                Button.inline("Да", data=f"power:run:{power_action}"),
                Button.inline("Нет", data="cancel:main"),
            ]
        ]
        await self._safe_edit_message(message, text, keyboard)

    async def _run_power_action(self, message: Message, power_action: str) -> None:
        messages = {
            "shutdown": "Команда отправлена: компьютер будет выключен.",
            "restart": "Команда отправлена: компьютер будет перезагружен.",
            "sleep": "Команда отправлена: компьютер перейдет в спящий режим.",
        }
        await self._safe_edit_message(message, messages[power_action], self._main_menu_keyboard())
        threading.Thread(target=self._perform_power_action_with_delay, args=(power_action,), daemon=True).start()

    async def _minimize_all_windows(self, message: Message) -> None:
        try:
            WindowsDesktop.minimize_all_windows()
        except Exception as error:  # noqa: BLE001
            logging.exception("Не удалось свернуть все приложения: %s", error)
            await self._show_main_menu(message, notice="Не удалось свернуть все приложения.")
            return
        await self._show_main_menu(message, notice="Все приложения свернуты.")

    async def _launch_single_catalog_app(self, message: Message, app_id: str, page: int) -> None:
        try:
            catalog = self._load_launch_catalog()
            app = self._find_launch_app(catalog, app_id)
            if app is None:
                raise LaunchCatalogError("Выбранное приложение уже не найдено.")
            notice = self._launch_app_entry(app)
        except LaunchCatalogError as error:
            await self._show_launches_page(message, page, notice=str(error))
            return
        except Exception as error:  # noqa: BLE001
            logging.exception("Не удалось запустить приложение %s: %s", app_id, error)
            await self._show_launches_page(message, page, notice="Не удалось запустить выбранное приложение.")
            return
        await self._show_launches_page(message, page, notice=notice)

    async def _launch_catalog_pack(self, message: Message, pack_id: str, page: int) -> None:
        try:
            catalog = self._load_launch_catalog()
            pack = self._find_launch_pack(catalog, pack_id)
            if pack is None:
                raise LaunchCatalogError("Выбранный пак уже не найден.")
            notice = self._launch_pack_entry(catalog, pack)
        except LaunchCatalogError as error:
            await self._show_launches_page(message, page, notice=str(error))
            return
        except Exception as error:  # noqa: BLE001
            logging.exception("Не удалось запустить пак %s: %s", pack_id, error)
            await self._show_launches_page(message, page, notice="Не удалось запустить выбранный пак.")
            return
        await self._show_launches_page(message, page, notice=notice)

    def _perform_power_action_with_delay(self, power_action: str) -> None:
        time.sleep(1)
        try:
            if power_action == "shutdown":
                subprocess.run(["shutdown", "/s", "/t", "0"], check=True)
            elif power_action == "restart":
                subprocess.run(["shutdown", "/r", "/t", "0"], check=True)
            elif power_action == "sleep":
                WindowsDesktop.sleep_computer()
        except Exception as error:  # noqa: BLE001
            logging.exception("Не удалось выполнить действие %s: %s", power_action, error)

    async def _safe_edit_message(self, message: Message, text: str, buttons) -> None:
        try:
            await message.edit(text, buttons=buttons, parse_mode="html")
        except MessageNotModifiedError:
            pass

    @staticmethod
    def _build_screen_text(text: str, notice: str | None = None) -> str:
        if not notice:
            return text
        return f"{html.escape(notice)}\n\n{text}"

    def _build_status_text(self) -> str:
        memory = psutil.virtual_memory()
        storage_used, storage_total, storage_count = self._get_storage_totals()
        uptime_seconds = max(0, int(time.time() - psutil.boot_time()))
        is_online = self.bot_status == "Онлайн"
        state_icon = "🟢" if is_online else "🔴"
        state_label = "онлайн" if is_online else "оффлайн"
        storage_percent = (storage_used / storage_total * 100) if storage_total else 0
        storage_label = "Накопитель" if storage_count == 1 else "Накопители"

        return "\n".join(
            [
                f"{state_icon} <b>Статус:</b> {state_label}",
                f"🧠 <b>ОЗУ:</b> {self._format_bytes(memory.used)} / {self._format_bytes(memory.total)} ({memory.percent}%)",
                f"💽 <b>{storage_label}:</b> {self._format_bytes(storage_used)} / {self._format_bytes(storage_total)} ({storage_percent:.1f}%)",
                f"⏱ <b>Работает:</b> {self._format_duration(uptime_seconds)}",
            ]
        )

    def _main_menu_keyboard(self):
        return [
            [Button.inline("🔄 Обновить", data="refresh")],
            [Button.inline("📋 Активные приложения", data="apps:0")],
            [Button.inline("🚀 Запуск приложений", data="launches:0")],
            [Button.inline("🪟 Свернуть все приложения", data="desktop")],
            [Button.inline("🔴 Выключить компьютер", data="power:confirm:shutdown")],
            [Button.inline("🔁 Перезагрузить компьютер", data="power:confirm:restart")],
            [Button.inline("🌙 Спящий режим", data="power:confirm:sleep")],
        ]

    def _apps_keyboard(
        self,
        token: str,
        page: int,
        total_pages: int,
        apps: list[AppGroup],
        start_index: int,
    ):
        rows = []
        for offset, app in enumerate(apps):
            rows.append(
                [
                    Button.inline(
                        self._shorten_label(self._app_button_label(app)),
                        data=f"app:{token}:{start_index + offset}:{page}",
                    )
                ]
            )

        navigation = []
        if page > 0:
            navigation.append(Button.inline("◀", data=f"apps:{page - 1}"))
        if page < total_pages - 1:
            navigation.append(Button.inline("▶", data=f"apps:{page + 1}"))
        if navigation:
            rows.append(navigation)

        rows.append([Button.inline("🔄 Обновить", data=f"apps:{page}")])
        rows.append([Button.inline("🏠 В главное меню", data="main")])
        return rows

    def _windows_keyboard(
        self,
        token: str,
        group_index: int,
        page: int,
        total_pages: int,
        windows: list[OpenWindow],
        start_index: int,
        apps_page: int,
    ):
        rows = []
        for offset, window in enumerate(windows):
            rows.append(
                [
                    Button.inline(
                        self._shorten_label(window.window_title),
                        data=f"win:{token}:{group_index}:{start_index + offset}:{page}:{apps_page}",
                    )
                ]
            )

        navigation = []
        if page > 0:
            navigation.append(Button.inline("◀", data=f"wins:{token}:{group_index}:{page - 1}:{apps_page}"))
        if page < total_pages - 1:
            navigation.append(Button.inline("▶", data=f"wins:{token}:{group_index}:{page + 1}:{apps_page}"))
        if navigation:
            rows.append(navigation)

        rows.append([Button.inline("⬅️ К приложениям", data=f"apps:{apps_page}")])
        rows.append([Button.inline("🔄 Обновить", data=f"wins:{token}:{group_index}:{page}:{apps_page}")])
        rows.append([Button.inline("🏠 В главное меню", data="main")])
        return rows

    def _apps_keyboard_for_back(self, page: int):
        return [
            [Button.inline("⬅️ К приложениям", data=f"apps:{page}")],
            [Button.inline("🏠 В главное меню", data="main")],
        ]

    def _launches_keyboard(self, items: list[LaunchMenuItem], page: int, total_pages: int):
        rows = []
        for item in items:
            prefix = "📦" if item.kind == "pack" else "▶️"
            action = "launchpack" if item.kind == "pack" else "launchapp"
            rows.append([Button.inline(self._shorten_label(f"{prefix} {item.title}"), data=f"{action}:{item.item_id}:{page}")])

        navigation = []
        if page > 0:
            navigation.append(Button.inline("◀", data=f"launches:{page - 1}"))
        if page < total_pages - 1:
            navigation.append(Button.inline("▶", data=f"launches:{page + 1}"))
        if navigation:
            rows.append(navigation)

        rows.append([Button.inline("🔄 Обновить", data=f"launches:{page}")])
        rows.append([Button.inline("🏠 В главное меню", data="main")])
        return rows

    def _launches_back_keyboard(self):
        return [
            [Button.inline("🔄 Обновить", data="launches:0")],
            [Button.inline("🏠 В главное меню", data="main")],
        ]

    def _store_apps_snapshot(self, groups: list[AppGroup]) -> str:
        with self.snapshot_lock:
            self.snapshot_serial += 1
            token = format(self.snapshot_serial, "x")
            self.apps_snapshots[token] = groups
            while len(self.apps_snapshots) > SNAPSHOT_LIMIT:
                oldest_token = next(iter(self.apps_snapshots))
                del self.apps_snapshots[oldest_token]
            return token

    def _get_snapshot_group(self, token: str, group_index: int) -> AppGroup | None:
        with self.snapshot_lock:
            groups = self.apps_snapshots.get(token)
        if groups is None or group_index < 0 or group_index >= len(groups):
            return None
        return groups[group_index]

    def _get_snapshot_window(self, token: str, group_index: int, window_index: int) -> OpenWindow | None:
        group = self._get_snapshot_group(token, group_index)
        if group is None or window_index < 0 or window_index >= len(group.windows):
            return None
        return group.windows[window_index]

    def _remember_preview_message(self, chat_id: int, message_id: int) -> None:
        with self.preview_lock:
            self.preview_messages[chat_id] = message_id

    async def _delete_preview_message(self, chat_id: int | None) -> None:
        if chat_id is None or self.client is None:
            return

        with self.preview_lock:
            message_id = self.preview_messages.pop(chat_id, None)
        if not message_id:
            return

        try:
            await self.client.delete_messages(chat_id, message_id)
        except Exception:  # noqa: BLE001
            logging.exception("Не удалось удалить временный скриншот для чата %s", chat_id)

    @staticmethod
    def _find_group_index_by_key(groups: list[AppGroup], group_key: str) -> int | None:
        for index, group in enumerate(groups):
            if group.group_key == group_key:
                return index
        return None

    @staticmethod
    def _app_button_label(app: AppGroup) -> str:
        return f"{app.app_name} | {TelegramPcControlApp._format_window_count(len(app.windows))}"

    @staticmethod
    def _build_launch_menu_items(catalog: LaunchCatalog) -> list[LaunchMenuItem]:
        items = [LaunchMenuItem(kind="pack", item_id=pack.pack_id, title=pack.title) for pack in catalog.packs]
        items.extend(LaunchMenuItem(kind="app", item_id=app.app_id, title=app.title) for app in catalog.apps)
        return items

    @staticmethod
    def _find_launch_app(catalog: LaunchCatalog, app_id: str) -> LaunchApp | None:
        for app in catalog.apps:
            if app.app_id == app_id:
                return app
        return None

    @staticmethod
    def _find_launch_pack(catalog: LaunchCatalog, pack_id: str) -> LaunchPack | None:
        for pack in catalog.packs:
            if pack.pack_id == pack_id:
                return pack
        return None

    def _launch_pack_entry(self, catalog: LaunchCatalog, pack: LaunchPack) -> str:
        launched_titles: list[str] = []
        for index, app_id in enumerate(pack.apps):
            app = self._find_launch_app(catalog, app_id)
            if app is None:
                raise LaunchCatalogError(f"В паке {pack.title} не найдено приложение с id {app_id}.")
            self._launch_app_entry(app)
            launched_titles.append(app.title)
            if index < len(pack.apps) - 1 and pack.delay_ms > 0:
                time.sleep(pack.delay_ms / 1000)

        preview = ", ".join(launched_titles[:4])
        if len(launched_titles) > 4:
            preview += f" и еще {len(launched_titles) - 4}"
        return f"Запущен пак «{pack.title}»: {preview}."

    def _launch_app_entry(self, app: LaunchApp) -> str:
        target = self._resolve_launch_target(app.target)
        start_in = self._resolve_launch_workdir(app.start_in)
        show_cmd = self._map_window_style(app.window_style)
        startfile_kwargs = {"show_cmd": show_cmd}
        if app.arguments:
            startfile_kwargs["arguments"] = app.arguments
        if start_in:
            startfile_kwargs["cwd"] = start_in

        try:
            os.startfile(target, **startfile_kwargs)
        except OSError as error:
            raise LaunchCatalogError(f"Не удалось запустить «{app.title}»: {error}") from error

        return f"Запускаю «{app.title}»."

    def _resolve_launch_target(self, raw_target: str) -> str:
        target = os.path.expandvars(os.path.expanduser(raw_target.strip()))
        if self._is_shell_target(target):
            return target

        path = Path(target)
        if not path.is_absolute():
            path = (self.root_dir / path).resolve()
        if not path.exists():
            raise LaunchCatalogError(f"Не найден target: {path}")
        return str(path)

    def _resolve_launch_workdir(self, raw_start_in: str) -> str:
        start_in = raw_start_in.strip()
        if not start_in:
            return ""

        path = Path(os.path.expandvars(os.path.expanduser(start_in)))
        if not path.is_absolute():
            path = (self.root_dir / path).resolve()
        if not path.exists():
            raise LaunchCatalogError(f"Не найдена папка start_in: {path}")
        return str(path)

    @staticmethod
    def _is_shell_target(target: str) -> bool:
        if "://" in target:
            return True
        if len(target) >= 3 and target[1] == ":" and target[0].isalpha() and target[2] in {"\\", "/"}:
            return False
        prefix, separator, _ = target.partition(":")
        if not separator or not prefix:
            return False
        return all(character.isalnum() or character in {"+", "-", "."} for character in prefix)

    @staticmethod
    def _map_window_style(window_style: str) -> int:
        mapping = {
            "normal": 1,
            "minimized": 2,
            "maximized": 3,
        }
        return mapping.get(window_style, 1)

    @staticmethod
    def _format_window_count(count: int) -> str:
        if count % 10 == 1 and count % 100 != 11:
            suffix = "окно"
        elif count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
            suffix = "окна"
        else:
            suffix = "окон"
        return f"{count} {suffix}"

    @staticmethod
    def _get_storage_totals() -> tuple[int, int, int]:
        total = 0
        used = 0
        seen_mounts: set[str] = set()
        for partition in psutil.disk_partitions(all=False):
            mountpoint = partition.mountpoint.rstrip("\\")
            if not mountpoint or mountpoint in seen_mounts:
                continue
            seen_mounts.add(mountpoint)
            if "cdrom" in partition.opts.casefold():
                continue
            try:
                usage = psutil.disk_usage(partition.mountpoint)
            except (PermissionError, OSError):
                continue
            total += usage.total
            used += usage.used
        return used, total, len(seen_mounts)

    @staticmethod
    def _shorten_label(text: str, max_length: int = 52) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= max_length:
            return normalized
        return normalized[: max_length - 1] + "…"

    @staticmethod
    def _format_bytes(value: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(value)
        unit_index = 0
        while size >= 1024 and unit_index < len(units) - 1:
            size /= 1024
            unit_index += 1
        return f"{size:.1f} {units[unit_index]}"

    @staticmethod
    def _format_duration(seconds: int) -> str:
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, secs = divmod(remainder, 60)

        parts: list[str] = []
        if days:
            parts.append(f"{days} д")
        if hours or days:
            parts.append(f"{hours} ч")
        if minutes or hours or days:
            parts.append(f"{minutes} мин")
        parts.append(f"{secs} сек")
        return " ".join(parts)

    def run_self_test(self) -> None:
        groups = WindowsDesktop.list_app_groups()
        proxy_configured = False
        launch_catalog_ok = False
        launch_items_count = 0
        try:
            self._load_config()
            proxy_configured = True
        except ConfigError:
            proxy_configured = False
        try:
            catalog = self._load_launch_catalog()
            launch_catalog_ok = True
            launch_items_count = len(catalog.apps) + len(catalog.packs)
        except LaunchCatalogError:
            launch_catalog_ok = False
        status = {
            "status_text": self._build_status_text(),
            "open_apps_count": len(groups),
            "sample_apps": [
                {
                    "app_name": group.app_name,
                    "windows": len(group.windows),
                    "titles": [window.window_title for window in group.windows[:3]],
                }
                for group in groups[:5]
            ],
            "autostart_command": self._build_launch_command(),
            "config_exists": self.config_path.exists(),
            "launch_catalog_exists": self.launch_catalog_path.exists(),
            "launch_catalog_ok": launch_catalog_ok,
            "launch_items_count": launch_items_count,
            "proxy_configured": proxy_configured,
        }
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(status, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram-бот для управления компьютером через трей.")
    parser.add_argument("--self-test", action="store_true", help="Показать локальную диагностику и выйти.")
    return parser.parse_args()


def main() -> None:
    root_dir = Path(__file__).resolve().parent
    app = TelegramPcControlApp(root_dir)
    args = parse_args()
    if args.self_test:
        app.run_self_test()
        return

    try:
        app.run()
    finally:
        app.stop()


if __name__ == "__main__":
    main()
