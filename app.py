"""Delta Force configuration assistant UI.

Provides a step-by-step Windows-friendly interface to locate the live
Engine.ini configuration, keep a local backup, swap in curated presets,
and monitor the game to automatically restore the backup once the game
is running.
"""

from __future__ import annotations

import json
import shutil
import stat
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import psutil
import tkinter as tk
from tkinter import messagebox
from tkinter import scrolledtext
from tkinter import ttk

from find_deltaforce_config import find_engine_configs
from jiance import find_game_executable

try:
    from plyer import notification
except Exception:  # pragma: no cover - plyer might be missing during lint
    notification = None  # type: ignore


APP_VERSION = "v1.0.0beta"


def _resolve_app_dir() -> Path:
    """Resolve the directory that should contain mutable files when frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resolve_resource_dir(app_dir: Path) -> Path:
    """Locate bundled resources (PyInstaller exposes them via sys._MEIPASS)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return app_dir


BASE_DIR = _resolve_app_dir()
RESOURCE_DIR = _resolve_resource_dir(BASE_DIR)
INI_DIR = BASE_DIR / "ini"
V5_DIR = INI_DIR / "v5"
BACKUP_DIR = INI_DIR / "yuan"
BACKUP_FILE = BACKUP_DIR / "Engine.ini"
SETTINGS_FILE = BASE_DIR / "settings.json"
DEFAULT_SEARCH_ROOT = Path(r"C:\\WeGameApps")

CHECK_INTERVAL_SECONDS = 3
DEFAULT_RESTORE_DELAY_SECONDS = 8


@dataclass
class MonitorCallbacks:
    on_log: Callable[[str], None]
    on_game_started: Callable[[], None]
    on_game_stopped: Callable[[], None]
    on_error: Callable[[str], None]


class ConfigMonitor(threading.Thread):
    """Background worker that watches the Delta Force process."""

    def __init__(self, exe_path: Path, callbacks: MonitorCallbacks, interval: int = CHECK_INTERVAL_SECONDS) -> None:
        super().__init__(daemon=True)
        self.exe_path = exe_path
        self.callbacks = callbacks
        self.interval = max(1, int(interval))
        self._stop_event = threading.Event()
        self._process_name = exe_path.stem.lower()

    # Public API -----------------------------------------------------------------

    def stop(self) -> None:
        self._stop_event.set()

    def is_game_running(self) -> bool:
        return _is_process_running(self._process_name, self.exe_path)

    # Thread ---------------------------------------------------------------------

    def run(self) -> None:  # pragma: no cover - thread tested indirectly
        last_running = False
        try:
            while not self._stop_event.is_set():
                running = self.is_game_running()
                if running and not last_running:
                    self.callbacks.on_log("检测到游戏进程启动。")
                    self.callbacks.on_game_started()
                elif not running and last_running:
                    self.callbacks.on_log("检测到游戏进程结束。")
                    self.callbacks.on_game_stopped()
                last_running = running

                # Wait in small slices so stop() responds promptly.
                for _ in range(self.interval * 10):
                    if self._stop_event.wait(0.1):
                        return
        except Exception as exc:  # pragma: no cover - defensive trap
            self.callbacks.on_error(f"监控线程发生异常: {exc}")
            raise


class ScrollableFrame(ttk.Frame):
    """A simple vertically scrollable container."""

    def __init__(self, parent: tk.Widget, **kwargs) -> None:
        super().__init__(parent, **kwargs)

        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.content = ttk.Frame(self.canvas)
        self.content.bind("<Configure>", self._on_content_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self.canvas_window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")

        # Bind mouse wheel events only when cursor is inside the scrollable area.
        self.content.bind("<Enter>", lambda _: self._bind_mousewheel())
        self.content.bind("<Leave>", lambda _: self._unbind_mousewheel())

    def _on_content_configure(self, event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.canvas_window, width=event.width)

    def _bind_mousewheel(self) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(-int(event.delta / 120), "units")


class DeltaForceConfigApp:
    """Tkinter application coordinating the three-step workflow."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"Delta Force 配置助手 {APP_VERSION} by chenni")
        self.root.geometry("760x680")
        self.root.minsize(640, 600)

        self.search_var = tk.StringVar(value=str(DEFAULT_SEARCH_ROOT))
        self.game_search_var = tk.StringVar(value=str(DEFAULT_SEARCH_ROOT))
        self.restore_delay_var = tk.IntVar(value=DEFAULT_RESTORE_DELAY_SECONDS)

        self.config_paths: list[Path] = []
        self.selected_config_path: Optional[Path] = None
        self.template_paths: list[Path] = []

        self.settings: dict[str, Any] = _load_settings()
        self._apply_loaded_settings()

        self.monitor_thread: Optional[ConfigMonitor] = None
        self.pending_restore_job: Optional[str] = None
        self.game_currently_running = False
        self._closing = False
        self._sponsor_window: Optional[tk.Toplevel] = None
        self._sponsor_image: Optional[tk.PhotoImage] = None

        self._build_ui()
        self._load_saved_config_path()
        self._refresh_templates()
        self._update_step_states()
        self.root.after(300, self._show_sponsor_popup)

    # UI construction -----------------------------------------------------------

    def _build_ui(self) -> None:
        scrollable = ScrollableFrame(self.root)
        scrollable.pack(fill=tk.BOTH, expand=True)

        main_frame = ttk.Frame(scrollable.content, padding=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        self._build_notice(main_frame)
        self._build_step1(main_frame)
        self._build_step2(main_frame)
        self._build_step3(main_frame)
        self._build_log_area(main_frame)

    def _build_notice(self, parent: ttk.Frame) -> None:
        notice_text = (
            "原理参考: https://www.bilibili.com/video/BV1xe1rBgE4G/\n"
            "本资源仅供学习交流，严禁用于商业用途，请于24小时内删除。"
        )
        ttk.Label(parent, text=notice_text, foreground="#b22222", wraplength=700, justify=tk.LEFT).pack(
            fill=tk.X, pady=(0, 10)
        )

    def _apply_loaded_settings(self) -> None:
        last_search = self.settings.get("last_search_paths")
        if isinstance(last_search, list) and last_search:
            self.search_var.set(";".join(str(item) for item in last_search if str(item).strip()))

        last_game_search = self.settings.get("last_game_search_paths")
        if isinstance(last_game_search, list) and last_game_search:
            self.game_search_var.set(";".join(str(item) for item in last_game_search if str(item).strip()))

        restore_delay = self.settings.get("restore_delay_seconds")
        if isinstance(restore_delay, int):
            clamped = max(0, min(120, restore_delay))
            self.restore_delay_var.set(clamped)

    def _load_saved_config_path(self) -> None:
        saved_path_value = self.settings.get("last_config_path")
        if not saved_path_value:
            return

        saved_path = Path(str(saved_path_value))
        if not saved_path.exists():
            self.log_message("此前保存的配置路径已失效，请重新查找。")
            self.config_paths = []
            self.config_list.delete(0, tk.END)
            self.selected_config_path = None
            snapshot = self._get_settings_snapshot()
            snapshot["last_config_path"] = None
            self.settings = snapshot
            _save_settings(snapshot)
            return

        self.config_paths = [saved_path]
        self.config_list.delete(0, tk.END)
        self.config_list.insert(tk.END, str(saved_path))
        self.config_list.selection_set(0)
        self.selected_config_path = saved_path
        self.step1_status.configure(text=f"已加载保存的配置: {saved_path}", foreground="#0a0")
        self.log_message(f"快捷载入配置路径: {saved_path}")
        self._update_step_states()

    def _persist_settings(self) -> None:
        snapshot = self._get_settings_snapshot()
        self.settings = snapshot
        _save_settings(snapshot)

    def _get_settings_snapshot(self) -> dict[str, Any]:
        return {
            "last_config_path": str(self.selected_config_path) if self.selected_config_path else None,
            "last_search_paths": _parse_search_paths(self.search_var.get()),
            "last_game_search_paths": _parse_search_paths(self.game_search_var.get()),
            "restore_delay_seconds": self._get_restore_delay_seconds(),
        }

    def _build_step1(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="步骤 1：定位并备份游戏配置", padding=10)
        frame.pack(fill=tk.X, expand=False, pady=(0, 10))

        ttk.Label(frame, text="搜索根目录（可用分号分隔多个路径）:").pack(anchor=tk.W)
        search_row = ttk.Frame(frame)
        search_row.pack(fill=tk.X, pady=4)
        search_entry = ttk.Entry(search_row, textvariable=self.search_var)
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(search_row, text="查找配置", command=self._search_configs).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(frame, text="找到的 Engine.ini:").pack(anchor=tk.W, pady=(8, 2))
        self.config_list = tk.Listbox(frame, height=4, exportselection=False)
        self.config_list.pack(fill=tk.X, expand=True)
        self.config_list.bind("<<ListboxSelect>>", lambda _: self._on_config_selected())

        action_row = ttk.Frame(frame)
        action_row.pack(fill=tk.X, pady=(8, 0))
        self.backup_button = ttk.Button(action_row, text="备份至 ini/yuan", command=self._backup_selected_config)
        self.backup_button.pack(side=tk.LEFT)

        self.step1_status = ttk.Label(frame, text="请先查找并选择配置文件。", foreground="#666")
        self.step1_status.pack(anchor=tk.W, pady=(6, 0))

    def _build_step2(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="步骤 2：选择预设并覆盖游戏配置", padding=10)
        frame.pack(fill=tk.X, expand=False, pady=(0, 10))

        ttk.Label(frame, text="可用预设 (ini/v5):").pack(anchor=tk.W)
        self.template_list = tk.Listbox(frame, height=6, exportselection=False)
        self.template_list.pack(fill=tk.BOTH, expand=True, pady=4)

        button_row = ttk.Frame(frame)
        button_row.pack(fill=tk.X)
        ttk.Button(button_row, text="刷新列表", command=self._refresh_templates).pack(side=tk.LEFT)
        self.replace_button = ttk.Button(button_row, text="使用所选预设替换", command=self._apply_template)
        self.replace_button.pack(side=tk.LEFT, padx=(8, 0))

        self.step2_status = ttk.Label(frame, text="等待第一步完成后再进行预设替换。", foreground="#666")
        self.step2_status.pack(anchor=tk.W, pady=(6, 0))

    def _build_step3(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="步骤 3：启动监控，自动还原本地配置", padding=10)
        frame.pack(fill=tk.X, expand=False, pady=(0, 10))

        ttk.Label(frame, text="监控游戏时使用的搜索根目录:").pack(anchor=tk.W)
        game_row = ttk.Frame(frame)
        game_row.pack(fill=tk.X, pady=4)
        game_entry = ttk.Entry(game_row, textvariable=self.game_search_var)
        game_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.detect_button = ttk.Button(game_row, text="开始检测", command=self._start_monitoring)
        self.detect_button.pack(side=tk.LEFT, padx=(8, 0))
        self.stop_button = ttk.Button(game_row, text="停止检测", command=self._stop_monitoring)
        self.stop_button.pack(side=tk.LEFT, padx=(8, 0))

        delay_row = ttk.Frame(frame)
        delay_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(delay_row, text="恢复延迟(秒):").pack(side=tk.LEFT)
        delay_spin = ttk.Spinbox(delay_row, from_=0, to=120, textvariable=self.restore_delay_var, width=5)
        delay_spin.pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(delay_row, text="游戏启动后等待再恢复本地备份").pack(side=tk.LEFT, padx=(8, 0))

        self.step3_status = ttk.Label(frame, text="准备开始监控。", foreground="#666")
        self.step3_status.pack(anchor=tk.W, pady=(6, 0))

    def _build_log_area(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="通知与操作日志", padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        self.log_widget = scrolledtext.ScrolledText(frame, height=10, state=tk.DISABLED)
        self.log_widget.pack(fill=tk.BOTH, expand=True)
        self.log_message("欢迎使用配置助手，请按步骤依次操作。")

    def _show_sponsor_popup(self) -> None:
        image_path = RESOURCE_DIR / "zanzhu.jpg"
        if not image_path.exists():
            return

        try:
            from PIL import Image, ImageTk  # type: ignore
        except Exception:
            self.log_message("提示：缺少 Pillow 库，无法显示赞助二维码。可运行 pip install pillow 解决。")
            return

        try:
            image = Image.open(image_path)
            image.thumbnail((520, 520), Image.LANCZOS)
            photo = ImageTk.PhotoImage(image)
        except Exception as exc:
            self.log_message(f"无法加载赞助二维码: {exc}")
            return

        popup = tk.Toplevel(self.root)
        popup.title("支持作者")
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.protocol("WM_DELETE_WINDOW", popup.destroy)

        container = ttk.Frame(popup, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(container, image=photo).pack()
        ttk.Label(container, text="扫码支持作者，感谢您的赞助！").pack(pady=(8, 0))
        ttk.Button(container, text="关闭", command=popup.destroy).pack(pady=(8, 0))

        self._sponsor_image = photo
        self._sponsor_window = popup

        popup.update_idletasks()
        root_x = self.root.winfo_x()
        root_y = self.root.winfo_y()
        root_w = self.root.winfo_width()
        root_h = self.root.winfo_height()
        popup_w = popup.winfo_width()
        popup_h = popup.winfo_height()
        pos_x = root_x + max(0, (root_w - popup_w) // 2)
        pos_y = root_y + max(0, (root_h - popup_h) // 2)
        popup.geometry(f"+{pos_x}+{pos_y}")

    # Step 1 --------------------------------------------------------------------

    def _search_configs(self) -> None:
        self.log_message("正在扫描配置文件，请稍候...")
        raw_paths = [p.strip() for p in self.search_var.get().split(";") if p.strip()]
        search_roots = [Path(p) for p in raw_paths] or [DEFAULT_SEARCH_ROOT]

        matches: list[Path] = []
        for root in search_roots:
            try:
                matches.extend(find_engine_configs([root]))
            except Exception as exc:
                self.log_message(f"扫描 {root} 时出错: {exc}")

        unique_matches = []
        seen = set()
        for path in matches:
            if path.exists() and path not in seen:
                unique_matches.append(path)
                seen.add(path)

        self.config_paths = unique_matches
        self.config_list.delete(0, tk.END)
        for item in self.config_paths:
            self.config_list.insert(tk.END, str(item))

        if self.config_paths:
            self.config_list.selection_set(0)
            self._on_config_selected()
            self.log_message(f"找到 {len(self.config_paths)} 个配置文件，请选择一个进行下一步。")
        else:
            self.selected_config_path = None
            self.step1_status.configure(text="未找到配置，请检查目录后重试。", foreground="#c00")
            self.log_message("未找到任何 Engine.ini，请确认游戏已安装。")

        self._persist_settings()
        self._update_step_states()

    def _on_config_selected(self) -> None:
        try:
            index = int(self.config_list.curselection()[0])
        except (IndexError, ValueError):
            self.selected_config_path = None
            self.step1_status.configure(text="请先选择一个配置文件。", foreground="#c00")
            self._update_step_states()
            return

        self.selected_config_path = self.config_paths[index]
        self.step1_status.configure(text=f"当前选择: {self.selected_config_path}", foreground="#0a0")
        self.log_message(f"已选择配置文件: {self.selected_config_path}")
        self._persist_settings()
        self._update_step_states()

    def _backup_selected_config(self) -> None:
        if not self.selected_config_path:
            messagebox.showwarning("提示", "请先选择要备份的配置路径。")
            return
        if not self.selected_config_path.exists():
            messagebox.showerror("错误", "选定的配置文件不存在，可能已被移动。")
            self._update_step_states()
            return

        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        try:
            _ensure_writable(BACKUP_FILE)
            shutil.copy2(self.selected_config_path, BACKUP_FILE)
        except Exception as exc:
            messagebox.showerror("备份失败", f"无法备份配置: {exc}")
            self.log_message(f"备份失败: {exc}")
            return

        messagebox.showinfo("备份完成", f"已保存备份到 {BACKUP_FILE}")
        self.log_message(f"已备份配置到 {BACKUP_FILE}")
        self._update_step_states()

    # Step 2 --------------------------------------------------------------------

    def _refresh_templates(self) -> None:
        self.template_paths = []
        self.template_list.delete(0, tk.END)
        if not V5_DIR.exists():
            self.log_message("未找到 ini/v5 目录，请确认文件存在。")
            return

        for child in sorted(V5_DIR.iterdir()):
            engine_file = child / "Engine.ini"
            if child.is_dir() and engine_file.exists():
                self.template_paths.append(engine_file)
                self.template_list.insert(tk.END, child.name)

        if not self.template_paths:
            self.log_message("ini/v5 下未找到任何可用预设。")
        else:
            self.template_list.selection_clear(0, tk.END)
            self.template_list.selection_set(0)
        self._update_step_states()

    def _apply_template(self) -> None:
        if not self.selected_config_path:
            messagebox.showwarning("提示", "请先在步骤 1 中选择并备份配置。")
            return
        if not BACKUP_FILE.exists():
            messagebox.showwarning("提示", "请先完成步骤 1 的备份再继续。")
            return

        try:
            index = int(self.template_list.curselection()[0])
        except (IndexError, ValueError):
            messagebox.showwarning("提示", "请选择一个预设。")
            return

        template_engine = self.template_paths[index]
        try:
            _ensure_writable(self.selected_config_path)
            shutil.copy2(template_engine, self.selected_config_path)
        except Exception as exc:
            messagebox.showerror("替换失败", f"无法覆盖配置: {exc}")
            self.log_message(f"替换失败: {exc}")
            return

        messagebox.showinfo("替换完成", f"已使用预设 {template_engine.parent.name} 覆盖游戏配置。")
        self.log_message(f"已将 {template_engine.parent.name} 覆盖到 {self.selected_config_path}")
        self._update_step_states()

    # Step 3 --------------------------------------------------------------------

    def _start_monitoring(self) -> None:
        if self.monitor_thread and self.monitor_thread.is_alive():
            messagebox.showinfo("提示", "监控已在运行中。")
            return
        if not self.selected_config_path:
            messagebox.showwarning("提示", "请先完成步骤 1 并选择配置。")
            return
        if not BACKUP_FILE.exists():
            messagebox.showwarning("提示", "未找到备份文件，请先执行备份。")
            return

        search_roots = [Path(p.strip()) for p in self.game_search_var.get().split(";") if p.strip()]
        if not search_roots:
            search_roots = [DEFAULT_SEARCH_ROOT]

        game_exe = find_game_executable(search_roots)
        if not game_exe:
            messagebox.showerror("错误", "未能自动找到游戏主程序，请检查路径。")
            self.log_message("未找到游戏可执行文件，无法启动监控。")
            return

        callbacks = MonitorCallbacks(
            on_log=self.log_message_threadsafe,
            on_game_started=self._game_started_threadsafe,
            on_game_stopped=self._game_stopped_threadsafe,
            on_error=self._monitor_error_threadsafe,
        )
        self.monitor_thread = ConfigMonitor(game_exe, callbacks)
        self.monitor_thread.start()

        self.step3_status.configure(text=f"监控中：{game_exe}", foreground="#0a0")
        self.log_message(f"开始监控游戏: {game_exe}")
        self._persist_settings()
        self._update_step_states()

    def _stop_monitoring(self) -> None:
        if not self.monitor_thread:
            return
        self.monitor_thread.stop()
        self.monitor_thread.join(timeout=2)
        self.monitor_thread = None

        if self.pending_restore_job:
            self.root.after_cancel(self.pending_restore_job)
            self.pending_restore_job = None

        self.game_currently_running = False
        self.step3_status.configure(text="监控已停止。", foreground="#666")
        self.log_message("已停止监控。")
        self._update_step_states()

    # Monitor callbacks ---------------------------------------------------------

    def log_message_threadsafe(self, message: str) -> None:
        self.root.after(0, lambda: self.log_message(message))

    def _game_started_threadsafe(self) -> None:
        self.root.after(0, self._on_game_started)

    def _game_stopped_threadsafe(self) -> None:
        self.root.after(0, self._on_game_stopped)

    def _monitor_error_threadsafe(self, message: str) -> None:
        self.root.after(0, lambda: self._handle_monitor_error(message))

    def _on_game_started(self) -> None:
        self.game_currently_running = True
        delay_seconds = self._get_restore_delay_seconds()
        if delay_seconds:
            log_msg = f"游戏已启动，将在 {delay_seconds} 秒后恢复本地备份。"
            notify_msg = f"检测到游戏运行，将在 {delay_seconds} 秒后恢复本地配置。"
        else:
            log_msg = "游戏已启动，将立即恢复本地备份。"
            notify_msg = "检测到游戏运行，将立即恢复本地配置。"
        self.log_message(log_msg)
        _send_notification("🎮 游戏启动", notify_msg)
        if self.pending_restore_job:
            self.root.after_cancel(self.pending_restore_job)
        delay_ms = max(0, delay_seconds) * 1000
        self.pending_restore_job = self.root.after(delay_ms, self._restore_backup_to_game)

    def _on_game_stopped(self) -> None:
        self.game_currently_running = False
        if self.pending_restore_job:
            self.root.after_cancel(self.pending_restore_job)
            self.pending_restore_job = None
        self.log_message("游戏已退出。")
        _send_notification("⏹ 游戏关闭", "检测到游戏已退出。")

    def _handle_monitor_error(self, message: str) -> None:
        self.log_message(message)
        messagebox.showerror("监控异常", message)
        self._stop_monitoring()

    def _restore_backup_to_game(self) -> None:
        self.pending_restore_job = None
        if not self.game_currently_running:
            self.log_message("游戏已停止，跳过恢复操作。")
            return
        if not self.selected_config_path or not self.selected_config_path.exists():
            self.log_message("未找到游戏配置文件，无法恢复。")
            return
        if not BACKUP_FILE.exists():
            self.log_message("备份文件缺失，无法恢复。")
            return

        try:
            _ensure_writable(self.selected_config_path)
            shutil.copy2(BACKUP_FILE, self.selected_config_path)
            _ensure_read_only(self.selected_config_path)
        except Exception as exc:
            self.log_message(f"恢复失败: {exc}")
            messagebox.showerror("恢复失败", f"无法写入配置: {exc}")
            return

        self.log_message("已将备份配置恢复到游戏目录并设置为只读。")
        _send_notification("✅ 配置已恢复", "备份已覆盖游戏配置并设置为只读。")

    # Utility -------------------------------------------------------------------

    def _get_restore_delay_seconds(self) -> int:
        try:
            raw_value = self.restore_delay_var.get()
        except tk.TclError:
            raw_value = DEFAULT_RESTORE_DELAY_SECONDS
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            value = DEFAULT_RESTORE_DELAY_SECONDS
        value = max(0, min(120, value))
        if value != raw_value:
            self.restore_delay_var.set(value)
        return value

    def log_message(self, message: str) -> None:
        self.log_widget.configure(state=tk.NORMAL)
        self.log_widget.insert(tk.END, f"{message}\n")
        self.log_widget.configure(state=tk.DISABLED)
        self.log_widget.see(tk.END)

    def _update_step_states(self) -> None:
        config_selected = self.selected_config_path is not None
        has_backup = BACKUP_FILE.exists()
        monitor_running = self.monitor_thread is not None and self.monitor_thread.is_alive()

        self.backup_button.config(state=tk.NORMAL if config_selected else tk.DISABLED)
        self.replace_button.config(state=tk.NORMAL if (config_selected and has_backup) else tk.DISABLED)
        self.detect_button.config(state=tk.DISABLED if monitor_running else (tk.NORMAL if config_selected and has_backup else tk.DISABLED))
        self.stop_button.config(state=tk.NORMAL if monitor_running else tk.DISABLED)

        if not config_selected:
            self.step2_status.configure(text="请先完成步骤 1 的备份。", foreground="#c00")
            self.step3_status.configure(text="等待步骤 1 完成。", foreground="#c00")
        elif not has_backup:
            self.step2_status.configure(text="请先点击“备份”以保存原始配置。", foreground="#c00")
            self.step3_status.configure(text="备份完成后即可启动监控。", foreground="#c00")
        else:
            self.step2_status.configure(text="可选择预设覆盖游戏配置。", foreground="#0a0")
            self.step3_status.configure(text="准备就绪，可随时启动监控。", foreground="#0a0")

    def on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._persist_settings()
        self._stop_monitoring()
        if self._sponsor_window and self._sponsor_window.winfo_exists():
            self._sponsor_window.destroy()
        if self.root.winfo_exists():
            self.root.destroy()


# Helper functions -------------------------------------------------------------

def _is_process_running(process_stem: str, exe_path: Path) -> bool:
    for process in psutil.process_iter(["name", "exe"]):
        try:
            name = process.info.get("name")
            if name and name.lower().startswith(process_stem):
                return True
            executable = process.info.get("exe")
            if executable and Path(executable).resolve() == exe_path.resolve():
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def _ensure_writable(target: Path) -> None:
    if not target.exists():
        return
    try:
        mode = target.stat().st_mode
        target.chmod(mode | stat.S_IWRITE)
    except Exception:
        pass


def _ensure_read_only(target: Path) -> None:
    if not target.exists():
        return
    try:
        mode = target.stat().st_mode
        target.chmod(mode & ~stat.S_IWRITE)
    except Exception:
        pass


def _send_notification(title: str, message: str) -> None:
    if notification is None:
        return
    try:
        notification.notify(title=title, message=message, timeout=5, app_name="Delta Force 配置助手")
    except Exception:
        pass


def _load_settings() -> dict[str, Any]:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        with SETTINGS_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_settings(data: dict[str, Any]) -> None:
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with SETTINGS_FILE.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
    except Exception:
        pass


def _parse_search_paths(raw_value: str) -> list[str]:
    return [part.strip() for part in raw_value.split(";") if part.strip()]


def main() -> None:
    root = tk.Tk()
    app = DeltaForceConfigApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
