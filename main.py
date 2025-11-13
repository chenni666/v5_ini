import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Dict, List, Optional, Set, Tuple

from chazhao import DEFAULT_ROOTS, find_game_configs, list_drive_roots


def apply_anti_aliasing_setting(config_file: Path) -> Tuple[str, bool]:
    """Apply the anti-aliasing tweak to the supplied configuration file."""
    if not config_file.exists():
        return "配置文件不存在。", False

    try:
        content = config_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = config_file.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            return f"读取配置文件失败: {exc}", False
    except OSError as exc:
        return f"读取配置文件失败: {exc}", False

    if "sg.AntiAliasingQuality=0" in content:
        return "抗锯齿设置已存在，无需修改。", False

    lines = content.splitlines()
    updated = False
    message = ""
    section_found = False
    in_section = False
    insertion_index = None

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section and insertion_index is None:
                insertion_index = idx
            in_section = stripped.lower() == "[scalabilitygroups]"
            if in_section:
                section_found = True
            continue

        if in_section and stripped.lower().startswith("sg.antialiasingquality"):
            if stripped == "sg.AntiAliasingQuality=0":
                return "抗锯齿设置已存在，无需修改。", False
            lines[idx] = "sg.AntiAliasingQuality=0"
            message = "已将现有抗锯齿设置更新为 0。"
            updated = True
            break

    if not updated and section_found:
        line_to_insert = "sg.AntiAliasingQuality=0"
        if insertion_index is None:
            lines.append(line_to_insert)
        else:
            lines.insert(insertion_index, line_to_insert)
        message = "已在 [ScalabilityGroups] 部分添加抗锯齿设置。"
        updated = True

    if not updated and not section_found:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(["[ScalabilityGroups]", "sg.AntiAliasingQuality=0"])
        message = "已添加新的 [ScalabilityGroups] 部分并写入抗锯齿设置。"
        updated = True

    if not updated:
        return "未进行任何修改。", False

    new_content = "\n".join(lines)
    if not new_content.endswith("\n"):
        new_content += "\n"

    try:
        config_file.write_text(new_content, encoding="utf-8")
    except OSError as exc:
        return f"写入配置文件失败: {exc}", False

    return message, True


def _strip_anti_aliasing_setting(content: str) -> Tuple[str, bool]:
    lines = content.splitlines()
    target = "sg.AntiAliasingQuality=0"
    header_token = "[scalabilitygroups]"

    start_idx: Optional[int] = None
    for idx, line in enumerate(lines):
        if line.strip().lower() == header_token:
            start_idx = idx
            break

    if start_idx is not None:
        end_idx = len(lines)
        for idx in range(start_idx + 1, len(lines)):
            stripped = lines[idx].strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                end_idx = idx
                break

        section = lines[start_idx + 1 : end_idx]
        filtered_section = [line for line in section if line.strip() != target]
        removed_in_section = len(filtered_section) != len(section)

        if removed_in_section:
            lines[start_idx + 1 : end_idx] = filtered_section

            if not any(line.strip() for line in filtered_section):
                del lines[start_idx:end_idx]

                while start_idx < len(lines) and not lines[start_idx].strip():
                    del lines[start_idx]

                prev_idx = start_idx - 1
                while prev_idx >= 0 and not lines[prev_idx].strip():
                    del lines[prev_idx]
                    prev_idx -= 1

            return "\n".join(lines), True

    trimmed_lines = [line for line in lines if line.strip() != target]
    removed = len(trimmed_lines) != len(lines)
    return "\n".join(trimmed_lines), removed


def remove_anti_aliasing_setting(config_file: Path) -> Tuple[str, bool]:
    """Remove the anti-aliasing tweak from the supplied configuration file."""
    if not config_file.exists():
        return "配置文件不存在。", False

    try:
        content = config_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = config_file.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            return f"读取配置文件失败: {exc}", False
    except OSError as exc:
        return f"读取配置文件失败: {exc}", False

    updated_content, changed = _strip_anti_aliasing_setting(content)
    if not changed:
        return "未检测到可删除的抗锯齿设置。", False

    if updated_content and not updated_content.endswith("\n"):
        updated_content += "\n"

    try:
        config_file.write_text(updated_content, encoding="utf-8")
    except OSError as exc:
        return f"写入配置文件失败: {exc}", False

    return "已移除抗锯齿设置，还原文件。", True


class DeltaForceAssistant:
    """Simple step-by-step UI for safely patching the configuration file."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("DeltaForce 抗锯齿v1.0.0 bychenni")

        self.config_files: List[Path] = []
        self.selected_file: Optional[Path] = None
        self.step1_completed = False
        self.already_written_prompted: Set[Path] = set()
        self.original_contents: Dict[Path, str] = {}

        self.setting_status_var = tk.StringVar(value="待检测")

        self.create_widgets()
        self.root.after_idle(self.show_usage_notice)

    def create_widgets(self) -> None:
        self.root.geometry("760x560")

        notice_frame = ttk.Frame(self.root)
        notice_frame.pack(fill="x", padx=10, pady=(10, 0))

        notice_label = tk.Label(
            notice_frame,
            text="仅供学习交流，严禁用于商业用途，请于24小时内删除。原理参考：",
        )
        notice_label.pack(side="left")

        link_label = tk.Label(
            notice_frame,
            text="https://www.bilibili.com/video/BV1bpC4BeEi6/",
            fg="blue",
            cursor="hand2",
        )
        link_label.pack(side="left")
        link_label.bind("<Button-1>", self.open_reference_link)

        step1_frame = ttk.LabelFrame(self.root, text="步骤 1: 检测路径与设置")
        step1_frame.pack(fill="x", padx=10, pady=10)

        self.detect_button = ttk.Button(step1_frame, text="执行检测", command=self.on_step1)
        self.detect_button.grid(row=0, column=0, padx=5, pady=5, sticky="w")

        ttk.Label(step1_frame, textvariable=self.setting_status_var).grid(
            row=0,
            column=1,
            padx=5,
            pady=5,
            sticky="w",
            columnspan=3,
        )

        self.config_listbox = tk.Listbox(step1_frame, height=5, exportselection=False)
        self.config_listbox.grid(row=1, column=0, columnspan=3, padx=5, pady=(0, 5), sticky="nsew")
        self.config_listbox.bind("<<ListboxSelect>>", self.on_select_config)

        scrollbar = ttk.Scrollbar(step1_frame, orient="vertical", command=self.config_listbox.yview)
        scrollbar.grid(row=1, column=3, padx=(0, 5), pady=(0, 5), sticky="ns")
        self.config_listbox.config(yscrollcommand=scrollbar.set)

        step1_frame.columnconfigure(0, weight=1)
        step1_frame.columnconfigure(1, weight=1)
        step1_frame.columnconfigure(2, weight=1)
        step1_frame.rowconfigure(1, weight=1)

        step2_frame = ttk.LabelFrame(self.root, text="步骤 2: 写入或删除抗锯齿设置")
        step2_frame.pack(fill="x", padx=10, pady=10)

        self.write_button = ttk.Button(
            step2_frame,
            text="写入设置",
            command=self.on_write,
            state=tk.DISABLED,
        )
        self.write_button.pack(side="left", padx=5, pady=5)

        self.delete_button = ttk.Button(
            step2_frame,
            text="删除设置",
            command=self.on_delete_setting,
            state=tk.DISABLED,
        )
        self.delete_button.pack(side="left", padx=5, pady=5)

        ttk.Label(
            step2_frame,
            text="如需恢复原配置，可使用删除设置按钮。",
        ).pack(side="left", padx=5, pady=5)

        log_frame = ttk.LabelFrame(self.root, text="操作日志")
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.log_widget = ScrolledText(log_frame, wrap="word", height=15, state="disabled")
        self.log_widget.pack(fill="both", expand=True, padx=5, pady=5)

    def show_usage_notice(self) -> None:
        messagebox.showinfo(
            "使用须知",
            "本软件用于关闭抗锯齿。\n由于超分辨率会覆盖所有抗锯齿，所以只能在关闭超分辨率的情况下使用。",
        )

    def on_step1(self) -> None:
        self.log("开始检测配置文件…")
        configs = find_game_configs(DEFAULT_ROOTS)

        if not configs:
            self.log("在默认路径未找到配置文件，尝试全盘搜索…")
            configs = find_game_configs(list_drive_roots())

        self.config_files = configs
        self.config_listbox.delete(0, tk.END)
        self.selected_file = None
        self.step1_completed = False
        self.write_button.config(state=tk.DISABLED)
        self.delete_button.config(state=tk.DISABLED)
        self.detect_button.config(state=tk.NORMAL)

        if not configs:
            self.setting_status_var.set("未找到配置文件")
            messagebox.showwarning("提示", "未找到任何 GameUserSettings.ini 文件。")
            return

        for cfg in configs:
            status = self.describe_setting_status(cfg)
            self.log(f"检测到 {cfg} —— {status}")
            self.config_listbox.insert(tk.END, str(cfg))

        self.step1_completed = True
        self.config_listbox.selection_set(0)
        self.config_listbox.event_generate("<<ListboxSelect>>")

    def on_select_config(self, _event: object = None) -> None:
        if not self.config_listbox.curselection():
            return

        index = self.config_listbox.curselection()[0]
        if index >= len(self.config_files):
            return

        selected = self.config_files[index]
        if self.selected_file != selected:
            self.log(f"选中配置文件: {selected}")

        self.selected_file = selected
        status = self.describe_setting_status(selected)
        self.setting_status_var.set(status)
        self.update_action_buttons(status)

        if status == "已写入抗锯齿设置" and selected not in self.already_written_prompted:
            messagebox.showinfo("提示", "当前配置已写入目标设置，请勿重复操作。")
            self.log("检测到配置已写入，建议无需再次写入。")
            self.already_written_prompted.add(selected)

    def on_write(self) -> None:
        if self.selected_file is None:
            messagebox.showwarning("提示", "请选择配置文件。")
            return

        current_status = self.describe_setting_status(self.selected_file)
        if current_status == "已写入抗锯齿设置":
            messagebox.showinfo("提示", "当前配置已写入目标设置，无需重复操作。")
            self.update_action_buttons(current_status)
            self.already_written_prompted.add(self.selected_file)
            return

        if not messagebox.askyesno("确认写入", "确认写入抗锯齿设置？该操作将修改配置文件。"):
            return

        try:
            original_content = self.selected_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                original_content = self.selected_file.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                messagebox.showerror("写入失败", f"读取配置文件失败: {exc}")
                self.log(f"读取 {self.selected_file} 失败: {exc}")
                return
        except OSError as exc:
            messagebox.showerror("写入失败", f"读取配置文件失败: {exc}")
            self.log(f"读取 {self.selected_file} 失败: {exc}")
            return

        message, changed = apply_anti_aliasing_setting(self.selected_file)
        if changed:
            messagebox.showinfo("写入完成", message)
            if self.selected_file not in self.original_contents:
                self.original_contents[self.selected_file] = original_content
        else:
            messagebox.showinfo("写入结果", message)

        self.log(message)
        status = self.describe_setting_status(self.selected_file)
        self.setting_status_var.set(status)
        self.update_action_buttons(status)

    def on_delete_setting(self) -> None:
        if self.selected_file is None:
            messagebox.showwarning("提示", "请选择配置文件。")
            return

        if not messagebox.askyesno("确认删除", "确认移除抗锯齿设置并恢复原始配置？"):
            return

        original_content = self.original_contents.get(self.selected_file)
        if original_content is not None:
            try:
                self.selected_file.write_text(original_content, encoding="utf-8")
            except OSError as exc:
                messagebox.showerror("删除失败", f"写入配置文件失败: {exc}")
                self.log(f"恢复原始内容失败: {exc}")
                return

            self.log("已恢复原始配置文件内容。")
            messagebox.showinfo("删除完成", "已恢复原始配置文件内容。")
            del self.original_contents[self.selected_file]
            self.already_written_prompted.discard(self.selected_file)
        else:
            message, changed = remove_anti_aliasing_setting(self.selected_file)
            if changed:
                messagebox.showinfo("删除完成", message)
                self.already_written_prompted.discard(self.selected_file)
            else:
                messagebox.showinfo("删除结果", message)

            self.log(message)
            if not changed:
                status = self.describe_setting_status(self.selected_file)
                self.setting_status_var.set(status)
                self.update_action_buttons(status)
                return

            self.original_contents.pop(self.selected_file, None)

        status = self.describe_setting_status(self.selected_file)
        self.setting_status_var.set(status)
        self.update_action_buttons(status)

    def describe_setting_status(self, config_file: Path) -> str:
        try:
            content = config_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = config_file.read_text(encoding="utf-8", errors="ignore")
            except OSError as exc:
                self.log(f"读取 {config_file} 失败: {exc}")
                return "无法读取文件"
        except OSError as exc:
            self.log(f"读取 {config_file} 失败: {exc}")
            return "无法读取文件"

        if "sg.AntiAliasingQuality=0" in content:
            return "已写入抗锯齿设置"
        if "sg.AntiAliasingQuality" in content:
            return "检测到不同的抗锯齿值"
        if "[ScalabilityGroups]" in content:
            return "缺少抗锯齿设置"
        return "缺少 ScalabilityGroups 部分"

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_widget.config(state="normal")
        self.log_widget.insert("end", f"[{timestamp}] {message}\n")
        self.log_widget.see("end")
        self.log_widget.config(state="disabled")

    def update_action_buttons(self, status: str) -> None:
        if not self.step1_completed or self.selected_file is None:
            self.write_button.config(state=tk.DISABLED)
            self.delete_button.config(state=tk.DISABLED)
            self.detect_button.config(state=tk.NORMAL)
            return

        if status.startswith("无法"):
            self.write_button.config(state=tk.DISABLED)
            self.delete_button.config(state=tk.DISABLED)
            self.detect_button.config(state=tk.NORMAL)
            return

        if status == "已写入抗锯齿设置":
            self.write_button.config(state=tk.DISABLED)
            self.delete_button.config(state=tk.NORMAL)
            self.detect_button.config(state=tk.DISABLED)
            return

        self.write_button.config(state=tk.NORMAL)
        self.delete_button.config(state=tk.DISABLED)
        self.detect_button.config(state=tk.NORMAL)

    def open_reference_link(self, _event: object = None) -> None:
        webbrowser.open("https://www.bilibili.com/video/BV1bpC4BeEi6/")

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    app = DeltaForceAssistant()
    app.run()


if __name__ == "__main__":
    main()