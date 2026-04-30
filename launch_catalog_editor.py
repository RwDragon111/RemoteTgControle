from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import END, MULTIPLE, SINGLE, StringVar, Tk, filedialog, messagebox
import tkinter as tk
from tkinter import ttk


LAUNCH_CATALOG_FILE = "launch_catalog.json"
LAUNCH_CATALOG_EXAMPLE_FILE = "launch_catalog.example.json"
WINDOW_STYLES = ("normal", "minimized", "maximized")
DEFAULT_DELAY_MS = 700


class LaunchCatalogError(Exception):
    """Raised when the launch catalog is missing or invalid."""


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


def default_catalog_payload() -> dict:
    return {"apps": [], "packs": []}


def repair_invalid_json_escapes(text: str) -> str:
    hex_digits = set("0123456789abcdefABCDEF")
    result: list[str] = []
    in_string = False
    index = 0

    while index < len(text):
        character = text[index]
        if not in_string:
            result.append(character)
            if character == '"':
                in_string = True
            index += 1
            continue

        if character == '"':
            in_string = False
            result.append(character)
            index += 1
            continue

        if character == "\\":
            if index + 1 >= len(text):
                result.append("\\\\")
                index += 1
                continue

            next_character = text[index + 1]
            if next_character in {'"', "\\", "/", "b", "f", "n", "r", "t"}:
                result.append("\\")
                result.append(next_character)
                index += 2
                continue

            unicode_tail = text[index + 2 : index + 6]
            if next_character == "u" and len(unicode_tail) == 4 and all(item in hex_digits for item in unicode_tail):
                result.append(text[index : index + 6])
                index += 6
                continue

            result.append("\\\\")
            index += 1
            continue

        result.append(character)
        index += 1

    return "".join(result)


def ensure_catalog_file(path: Path) -> None:
    if path.exists():
        return
    example_path = path.with_name(LAUNCH_CATALOG_EXAMPLE_FILE)
    if example_path.exists():
        path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
        return
    path.write_text(json.dumps(default_catalog_payload(), ensure_ascii=False, indent=2), encoding="utf-8")


def load_catalog_from_disk(path: Path) -> tuple[LaunchCatalog, str | None]:
    ensure_catalog_file(path)
    text = path.read_text(encoding="utf-8")
    notice = None

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        repaired = repair_invalid_json_escapes(text)
        if repaired == text:
            raise LaunchCatalogError(f"{LAUNCH_CATALOG_FILE} поврежден: {error}") from error

        try:
            raw = json.loads(repaired)
        except json.JSONDecodeError as repaired_error:
            raise LaunchCatalogError(f"{LAUNCH_CATALOG_FILE} поврежден: {repaired_error}") from repaired_error

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = path.with_name(f"{path.stem}.broken-{timestamp}{path.suffix}")
        backup_path.write_text(text, encoding="utf-8")
        path.write_text(repaired, encoding="utf-8")
        notice = (
            "Файл launch_catalog.json был автоматически исправлен. "
            f"Резервная копия сохранена как {backup_path.name}."
        )

    return deserialize_catalog(raw), notice


def deserialize_catalog(raw: object) -> LaunchCatalog:
    if not isinstance(raw, dict):
        raise LaunchCatalogError("launch_catalog.json должен содержать объект с полями apps и packs.")

    raw_apps = raw.get("apps", [])
    raw_packs = raw.get("packs", [])
    if not isinstance(raw_apps, list):
        raise LaunchCatalogError("Поле apps должно быть списком.")
    if not isinstance(raw_packs, list):
        raise LaunchCatalogError("Поле packs должно быть списком.")

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
        if window_style not in WINDOW_STYLES:
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
            delay_ms = int(item.get("delay_ms", DEFAULT_DELAY_MS))
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

        app_ids = [str(value).strip() for value in raw_pack_apps if str(value).strip()]
        if not app_ids:
            raise LaunchCatalogError(f"В packs #{index} нет ни одного приложения.")

        packs.append(LaunchPack(pack_id=pack_id, title=title, apps=app_ids, delay_ms=delay_ms))
        seen_pack_ids.add(pack_id)

    known_app_ids = {app.app_id for app in apps}
    for pack in packs:
        missing = [item for item in pack.apps if item not in known_app_ids]
        if missing:
            raise LaunchCatalogError(
                f"Пак {pack.title} ссылается на несуществующие apps id: {', '.join(missing)}"
            )

    return LaunchCatalog(apps=apps, packs=packs)


def save_catalog_to_disk(path: Path, apps: list[LaunchApp], packs: list[LaunchPack]) -> None:
    payload = {
        "apps": [
            {
                "id": item.app_id,
                "title": item.title,
                "target": item.target,
                "arguments": item.arguments,
                "start_in": item.start_in,
                "window_style": item.window_style,
            }
            for item in apps
        ],
        "packs": [
            {
                "id": item.pack_id,
                "title": item.title,
                "apps": item.apps,
                "delay_ms": item.delay_ms,
            }
            for item in packs
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def slugify(value: str, fallback_prefix: str, existing_ids: set[str]) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_value).strip("_").casefold()
    if not slug:
        slug = fallback_prefix

    candidate = slug
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{slug}_{suffix}"
        suffix += 1
    return candidate


class LaunchCatalogEditor:
    def __init__(self, root: Tk, catalog_path: Path) -> None:
        self.root = root
        self.catalog_path = catalog_path
        self.apps: list[LaunchApp] = []
        self.packs: list[LaunchPack] = []
        self.current_app_index: int | None = None
        self.current_pack_index: int | None = None
        self.current_app_id: str | None = None
        self.current_pack_id: str | None = None
        self.status_var = StringVar(value=f"Файл: {self.catalog_path.name}")

        self.root.title("Редактор запусков")
        self.root.geometry("1120x760")
        self.root.minsize(980, 680)
        self.root.configure(bg="#f5f7fb")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self._configure_style()
        self._build_ui()
        self._load_catalog()

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Root.TFrame", background="#f5f7fb")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Section.TLabelframe", background="#ffffff")
        style.configure("Section.TLabelframe.Label", background="#ffffff", font=("Segoe UI", 10, "bold"))
        style.configure("TLabel", background="#f5f7fb", font=("Segoe UI", 10))
        style.configure("Hint.TLabel", background="#ffffff", foreground="#5f6b7a", font=("Segoe UI", 9))
        style.configure("Header.TLabel", background="#f5f7fb", foreground="#18212f", font=("Segoe UI Semibold", 16))
        style.configure("SubHeader.TLabel", background="#f5f7fb", foreground="#5f6b7a", font=("Segoe UI", 10))
        style.configure("Primary.TButton", font=("Segoe UI Semibold", 10))

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, style="Root.TFrame", padding=16)
        container.pack(fill="both", expand=True)

        ttk.Label(container, text="Редактор запусков", style="Header.TLabel").pack(anchor="w")
        ttk.Label(
            container,
            text="Добавляйте одиночные приложения и готовые паки без ручного редактирования JSON.",
            style="SubHeader.TLabel",
        ).pack(anchor="w", pady=(2, 12))

        notebook = ttk.Notebook(container)
        notebook.pack(fill="both", expand=True)
        self.notebook = notebook

        apps_tab = ttk.Frame(notebook, style="Root.TFrame", padding=8)
        packs_tab = ttk.Frame(notebook, style="Root.TFrame", padding=8)
        notebook.add(apps_tab, text="Приложения")
        notebook.add(packs_tab, text="Паки")

        self._build_apps_tab(apps_tab)
        self._build_packs_tab(packs_tab)

        footer = ttk.Frame(container, style="Root.TFrame")
        footer.pack(fill="x", pady=(12, 0))

        ttk.Label(footer, textvariable=self.status_var).pack(side="left")

        buttons = ttk.Frame(footer, style="Root.TFrame")
        buttons.pack(side="right")
        ttk.Button(buttons, text="Перечитать файл", command=self._reload_from_disk).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Открыть папку", command=self._open_folder).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Сохранить каталог", style="Primary.TButton", command=self._save_catalog).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Закрыть", command=self.on_close).pack(side="left")

    def _build_apps_tab(self, parent: ttk.Frame) -> None:
        layout = ttk.Frame(parent, style="Root.TFrame")
        layout.pack(fill="both", expand=True)
        layout.columnconfigure(0, weight=1)
        layout.columnconfigure(1, weight=2)
        layout.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(layout, text="Список приложений", style="Section.TLabelframe", padding=12)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.app_listbox = tk.Listbox(
            left,
            activestyle="none",
            exportselection=False,
            font=("Segoe UI", 10),
            selectmode=SINGLE,
            bg="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#d8dde6",
            highlightcolor="#7aa2ff",
        )
        self.app_listbox.grid(row=0, column=0, sticky="nsew")
        app_scroll = ttk.Scrollbar(left, orient="vertical", command=self.app_listbox.yview)
        app_scroll.grid(row=0, column=1, sticky="ns")
        self.app_listbox.config(yscrollcommand=app_scroll.set)
        self.app_listbox.bind("<<ListboxSelect>>", self._on_app_selected)

        app_buttons = ttk.Frame(left, style="Card.TFrame")
        app_buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(app_buttons, text="Новая запись", command=self._new_app).pack(side="left")
        ttk.Button(app_buttons, text="Удалить", command=self._delete_app).pack(side="left", padx=(8, 0))

        right = ttk.LabelFrame(layout, text="Параметры приложения", style="Section.TLabelframe", padding=12)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1)

        self.app_id_var = StringVar(value="Будет создан автоматически")
        self.app_title_var = StringVar()
        self.app_target_var = StringVar()
        self.app_arguments_var = StringVar()
        self.app_start_in_var = StringVar()
        self.app_window_style_var = StringVar(value="normal")

        ttk.Label(right, text="ID").grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Label(right, textvariable=self.app_id_var, style="Hint.TLabel").grid(row=0, column=1, sticky="w", pady=(0, 6))

        ttk.Label(right, text="Название").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(right, textvariable=self.app_title_var).grid(row=1, column=1, sticky="ew", pady=6, padx=(12, 0))

        ttk.Label(right, text="Путь или ссылка").grid(row=2, column=0, sticky="w", pady=6)
        path_row = ttk.Frame(right, style="Card.TFrame")
        path_row.grid(row=2, column=1, sticky="ew", pady=6, padx=(12, 0))
        path_row.columnconfigure(0, weight=1)
        ttk.Entry(path_row, textvariable=self.app_target_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(path_row, text="Обзор", command=self._browse_app_target).grid(row=0, column=1, padx=(8, 0))

        ttk.Label(right, text="Аргументы").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Entry(right, textvariable=self.app_arguments_var).grid(row=3, column=1, sticky="ew", pady=6, padx=(12, 0))

        ttk.Label(right, text="Рабочая папка").grid(row=4, column=0, sticky="w", pady=6)
        start_row = ttk.Frame(right, style="Card.TFrame")
        start_row.grid(row=4, column=1, sticky="ew", pady=6, padx=(12, 0))
        start_row.columnconfigure(0, weight=1)
        ttk.Entry(start_row, textvariable=self.app_start_in_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(start_row, text="Папка", command=self._browse_app_start_in).grid(row=0, column=1, padx=(8, 0))

        ttk.Label(right, text="Режим окна").grid(row=5, column=0, sticky="w", pady=6)
        ttk.Combobox(
            right,
            textvariable=self.app_window_style_var,
            values=WINDOW_STYLES,
            state="readonly",
        ).grid(row=5, column=1, sticky="w", pady=6, padx=(12, 0))

        ttk.Label(
            right,
            text="Можно указывать путь к .exe, .lnk, папке, а также URL или shell-ссылку.",
            style="Hint.TLabel",
            wraplength=460,
            justify="left",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 14))

        action_row = ttk.Frame(right, style="Card.TFrame")
        action_row.grid(row=7, column=0, columnspan=2, sticky="w")
        ttk.Button(action_row, text="Сохранить приложение", style="Primary.TButton", command=self._save_app_from_form).pack(side="left")
        ttk.Button(action_row, text="Очистить форму", command=self._new_app).pack(side="left", padx=(8, 0))

    def _build_packs_tab(self, parent: ttk.Frame) -> None:
        layout = ttk.Frame(parent, style="Root.TFrame")
        layout.pack(fill="both", expand=True)
        layout.columnconfigure(0, weight=1)
        layout.columnconfigure(1, weight=2)
        layout.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(layout, text="Список паков", style="Section.TLabelframe", padding=12)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.pack_listbox = tk.Listbox(
            left,
            activestyle="none",
            exportselection=False,
            font=("Segoe UI", 10),
            selectmode=SINGLE,
            bg="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#d8dde6",
            highlightcolor="#7aa2ff",
        )
        self.pack_listbox.grid(row=0, column=0, sticky="nsew")
        pack_scroll = ttk.Scrollbar(left, orient="vertical", command=self.pack_listbox.yview)
        pack_scroll.grid(row=0, column=1, sticky="ns")
        self.pack_listbox.config(yscrollcommand=pack_scroll.set)
        self.pack_listbox.bind("<<ListboxSelect>>", self._on_pack_selected)

        pack_buttons = ttk.Frame(left, style="Card.TFrame")
        pack_buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(pack_buttons, text="Новая запись", command=self._new_pack).pack(side="left")
        ttk.Button(pack_buttons, text="Удалить", command=self._delete_pack).pack(side="left", padx=(8, 0))

        right = ttk.LabelFrame(layout, text="Параметры пака", style="Section.TLabelframe", padding=12)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1)
        right.rowconfigure(2, weight=1)

        self.pack_id_var = StringVar(value="Будет создан автоматически")
        self.pack_title_var = StringVar()
        self.pack_delay_var = StringVar(value=str(DEFAULT_DELAY_MS))

        ttk.Label(right, text="ID").grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Label(right, textvariable=self.pack_id_var, style="Hint.TLabel").grid(row=0, column=1, sticky="w", pady=(0, 6))

        ttk.Label(right, text="Название").grid(row=1, column=0, sticky="w", pady=6)
        ttk.Entry(right, textvariable=self.pack_title_var).grid(row=1, column=1, sticky="ew", pady=6, padx=(12, 0))

        ttk.Label(right, text="Приложения в паке").grid(row=2, column=0, sticky="nw", pady=6)
        apps_selector = ttk.Frame(right, style="Card.TFrame")
        apps_selector.grid(row=2, column=1, sticky="nsew", pady=6, padx=(12, 0))
        apps_selector.columnconfigure(0, weight=1)
        apps_selector.rowconfigure(0, weight=1)

        self.pack_apps_listbox = tk.Listbox(
            apps_selector,
            activestyle="none",
            exportselection=False,
            font=("Segoe UI", 10),
            selectmode=MULTIPLE,
            bg="#ffffff",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#d8dde6",
            highlightcolor="#7aa2ff",
        )
        self.pack_apps_listbox.grid(row=0, column=0, sticky="nsew")
        pack_apps_scroll = ttk.Scrollbar(apps_selector, orient="vertical", command=self.pack_apps_listbox.yview)
        pack_apps_scroll.grid(row=0, column=1, sticky="ns")
        self.pack_apps_listbox.config(yscrollcommand=pack_apps_scroll.set)

        ttk.Label(right, text="Пауза между приложениями, мс").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Spinbox(right, from_=0, to=10000, increment=100, textvariable=self.pack_delay_var, width=10).grid(
            row=3,
            column=1,
            sticky="w",
            pady=6,
            padx=(12, 0),
        )

        ttk.Label(
            right,
            text="Выберите сразу несколько приложений, и они будут запускаться по одной кнопке.",
            style="Hint.TLabel",
            wraplength=460,
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 14))

        action_row = ttk.Frame(right, style="Card.TFrame")
        action_row.grid(row=5, column=0, columnspan=2, sticky="w")
        ttk.Button(action_row, text="Сохранить пак", style="Primary.TButton", command=self._save_pack_from_form).pack(side="left")
        ttk.Button(action_row, text="Очистить форму", command=self._new_pack).pack(side="left", padx=(8, 0))

    def _load_catalog(self) -> None:
        catalog, notice = load_catalog_from_disk(self.catalog_path)
        self.apps = catalog.apps
        self.packs = catalog.packs
        self._refresh_all_lists()
        self._new_app()
        self._new_pack()

        if notice:
            self.status_var.set(notice)
            messagebox.showwarning("Каталог исправлен", notice, parent=self.root)
        else:
            self.status_var.set(f"Каталог загружен: {self.catalog_path.name}")

    def _reload_from_disk(self) -> None:
        if messagebox.askyesno("Перечитать файл", "Несохраненные изменения в редакторе будут потеряны. Продолжить?", parent=self.root):
            self._load_catalog()

    def _refresh_all_lists(self) -> None:
        self._refresh_app_listbox()
        self._refresh_pack_listbox()
        self._refresh_pack_apps_listbox()

    def _refresh_app_listbox(self) -> None:
        self.app_listbox.delete(0, END)
        for app in self.apps:
            label = f"{app.title} — {Path(app.target).name if app.target else app.target}"
            self.app_listbox.insert(END, label)

    def _refresh_pack_listbox(self) -> None:
        self.pack_listbox.delete(0, END)
        for pack in self.packs:
            label = f"{pack.title} — {len(pack.apps)} прилож."
            self.pack_listbox.insert(END, label)

    def _refresh_pack_apps_listbox(self) -> None:
        previous_titles = self._selected_pack_app_ids()
        self.pack_apps_listbox.delete(0, END)
        for app in self.apps:
            self.pack_apps_listbox.insert(END, app.title)

        if previous_titles:
            for index, app in enumerate(self.apps):
                if app.app_id in previous_titles:
                    self.pack_apps_listbox.selection_set(index)

    def _selected_pack_app_ids(self) -> set[str]:
        return {self.apps[index].app_id for index in self.pack_apps_listbox.curselection() if index < len(self.apps)}

    def _on_app_selected(self, _: object) -> None:
        selection = self.app_listbox.curselection()
        if not selection:
            return
        self._load_app_into_form(selection[0])

    def _on_pack_selected(self, _: object) -> None:
        selection = self.pack_listbox.curselection()
        if not selection:
            return
        self._load_pack_into_form(selection[0])

    def _load_app_into_form(self, index: int) -> None:
        app = self.apps[index]
        self.current_app_index = index
        self.current_app_id = app.app_id
        self.app_id_var.set(app.app_id)
        self.app_title_var.set(app.title)
        self.app_target_var.set(app.target)
        self.app_arguments_var.set(app.arguments)
        self.app_start_in_var.set(app.start_in)
        self.app_window_style_var.set(app.window_style)

    def _load_pack_into_form(self, index: int) -> None:
        pack = self.packs[index]
        self.current_pack_index = index
        self.current_pack_id = pack.pack_id
        self.pack_id_var.set(pack.pack_id)
        self.pack_title_var.set(pack.title)
        self.pack_delay_var.set(str(pack.delay_ms))
        self.pack_apps_listbox.selection_clear(0, END)
        selected_ids = set(pack.apps)
        for app_index, app in enumerate(self.apps):
            if app.app_id in selected_ids:
                self.pack_apps_listbox.selection_set(app_index)

    def _new_app(self) -> None:
        self.current_app_index = None
        self.current_app_id = None
        self.app_id_var.set("Будет создан автоматически")
        self.app_title_var.set("")
        self.app_target_var.set("")
        self.app_arguments_var.set("")
        self.app_start_in_var.set("")
        self.app_window_style_var.set("normal")
        self.app_listbox.selection_clear(0, END)

    def _new_pack(self) -> None:
        self.current_pack_index = None
        self.current_pack_id = None
        self.pack_id_var.set("Будет создан автоматически")
        self.pack_title_var.set("")
        self.pack_delay_var.set(str(DEFAULT_DELAY_MS))
        self.pack_apps_listbox.selection_clear(0, END)
        self.pack_listbox.selection_clear(0, END)

    def _browse_app_target(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Выберите приложение",
            filetypes=[
                ("Приложения и ярлыки", "*.exe *.lnk *.bat *.cmd"),
                ("Все файлы", "*.*"),
            ],
        )
        if path:
            self.app_target_var.set(path)

    def _browse_app_start_in(self) -> None:
        path = filedialog.askdirectory(parent=self.root, title="Выберите рабочую папку")
        if path:
            self.app_start_in_var.set(path)

    def _save_app_from_form(self) -> bool:
        title = self.app_title_var.get().strip()
        target = self.app_target_var.get().strip()
        arguments = self.app_arguments_var.get().strip()
        start_in = self.app_start_in_var.get().strip()
        window_style = self.app_window_style_var.get().strip() or "normal"

        if not title:
            messagebox.showerror("Приложение", "Укажите название приложения.", parent=self.root)
            return False
        if not target:
            messagebox.showerror("Приложение", "Укажите путь или ссылку для запуска.", parent=self.root)
            return False
        if window_style not in WINDOW_STYLES:
            messagebox.showerror("Приложение", "Неверный режим окна.", parent=self.root)
            return False

        existing_ids = {app.app_id for idx, app in enumerate(self.apps) if idx != self.current_app_index}
        app_id = self.current_app_id or slugify(title, "app", existing_ids)
        app = LaunchApp(
            app_id=app_id,
            title=title,
            target=target,
            arguments=arguments,
            start_in=start_in,
            window_style=window_style,
        )

        if self.current_app_index is None:
            self.apps.append(app)
            self.current_app_index = len(self.apps) - 1
        else:
            self.apps[self.current_app_index] = app

        self.current_app_id = app.app_id
        self.app_id_var.set(app.app_id)
        self._refresh_app_listbox()
        self._refresh_pack_apps_listbox()
        self.app_listbox.selection_clear(0, END)
        self.app_listbox.selection_set(self.current_app_index)
        self.status_var.set(f"Приложение «{app.title}» сохранено в редакторе.")
        return True

    def _save_pack_from_form(self) -> bool:
        title = self.pack_title_var.get().strip()
        if not title:
            messagebox.showerror("Пак", "Укажите название пака.", parent=self.root)
            return False

        selected_indices = self.pack_apps_listbox.curselection()
        if not selected_indices:
            messagebox.showerror("Пак", "Выберите хотя бы одно приложение для пака.", parent=self.root)
            return False

        try:
            delay_ms = int(self.pack_delay_var.get().strip() or DEFAULT_DELAY_MS)
        except ValueError:
            messagebox.showerror("Пак", "Пауза между приложениями должна быть числом.", parent=self.root)
            return False

        if delay_ms < 0:
            messagebox.showerror("Пак", "Пауза между приложениями не может быть отрицательной.", parent=self.root)
            return False

        app_ids = [self.apps[index].app_id for index in selected_indices if index < len(self.apps)]
        existing_ids = {pack.pack_id for idx, pack in enumerate(self.packs) if idx != self.current_pack_index}
        pack_id = self.current_pack_id or slugify(title, "pack", existing_ids)
        pack = LaunchPack(pack_id=pack_id, title=title, apps=app_ids, delay_ms=delay_ms)

        if self.current_pack_index is None:
            self.packs.append(pack)
            self.current_pack_index = len(self.packs) - 1
        else:
            self.packs[self.current_pack_index] = pack

        self.current_pack_id = pack.pack_id
        self.pack_id_var.set(pack.pack_id)
        self._refresh_pack_listbox()
        self.pack_listbox.selection_clear(0, END)
        self.pack_listbox.selection_set(self.current_pack_index)
        self.status_var.set(f"Пак «{pack.title}» сохранен в редакторе.")
        return True

    def _delete_app(self) -> None:
        selection = self.app_listbox.curselection()
        if not selection:
            messagebox.showinfo("Приложения", "Сначала выберите приложение слева.", parent=self.root)
            return

        index = selection[0]
        app = self.apps[index]
        if not messagebox.askyesno(
            "Удалить приложение",
            f"Удалить приложение «{app.title}»?\n\nОно также будет убрано из всех паков.",
            parent=self.root,
        ):
            return

        del self.apps[index]
        for pack in self.packs:
            pack.apps = [item for item in pack.apps if item != app.app_id]

        self._refresh_all_lists()
        self._new_app()
        self.status_var.set(f"Приложение «{app.title}» удалено.")

    def _delete_pack(self) -> None:
        selection = self.pack_listbox.curselection()
        if not selection:
            messagebox.showinfo("Паки", "Сначала выберите пак слева.", parent=self.root)
            return

        index = selection[0]
        pack = self.packs[index]
        if not messagebox.askyesno("Удалить пак", f"Удалить пак «{pack.title}»?", parent=self.root):
            return

        del self.packs[index]
        self._refresh_pack_listbox()
        self._new_pack()
        self.status_var.set(f"Пак «{pack.title}» удален.")

    def _save_catalog(self) -> None:
        current_tab = self.notebook.index(self.notebook.select())
        if current_tab == 0 and self._app_form_has_content():
            if not self._save_app_from_form():
                return
        if current_tab == 1 and self._pack_form_has_content():
            if not self._save_pack_from_form():
                return

        empty_packs = [pack.title for pack in self.packs if not pack.apps]
        if empty_packs:
            messagebox.showerror(
                "Сохранение",
                "Некоторые паки не содержат ни одного приложения. Откройте их и выберите приложения заново.",
                parent=self.root,
            )
            return

        try:
            save_catalog_to_disk(self.catalog_path, self.apps, self.packs)
        except OSError as error:
            messagebox.showerror("Сохранение", f"Не удалось сохранить каталог:\n{error}", parent=self.root)
            return

        self.status_var.set(f"Каталог сохранен: {self.catalog_path.name}")
        messagebox.showinfo("Сохранение", "Каталог запусков сохранен.", parent=self.root)

    def _app_form_has_content(self) -> bool:
        return any(
            (
                self.app_title_var.get().strip(),
                self.app_target_var.get().strip(),
                self.app_arguments_var.get().strip(),
                self.app_start_in_var.get().strip(),
            )
        )

    def _pack_form_has_content(self) -> bool:
        return bool(self.pack_title_var.get().strip()) or bool(self.pack_apps_listbox.curselection())

    def _open_folder(self) -> None:
        explorer_target = self.catalog_path.parent
        try:
            tk._default_root.update_idletasks()
            import subprocess

            subprocess.Popen(["explorer.exe", str(explorer_target)])
        except Exception as error:  # noqa: BLE001
            messagebox.showerror("Папка", f"Не удалось открыть папку:\n{error}", parent=self.root)

    def on_close(self) -> None:
        self.root.destroy()


def main() -> None:
    root = Tk()
    catalog_path = Path(__file__).resolve().parent / LAUNCH_CATALOG_FILE

    try:
        LaunchCatalogEditor(root, catalog_path)
    except LaunchCatalogError as error:
        messagebox.showerror("Редактор запусков", str(error), parent=root)
        root.destroy()
        return

    root.mainloop()


if __name__ == "__main__":
    main()
