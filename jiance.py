import psutil
import time
import os
import argparse
from pathlib import Path
from typing import Optional
from plyer import notification
import threading

class GameMonitor:
    def __init__(self, game_path: Path, check_interval=5):
        """
        初始化游戏监控器
        
        Args:
            game_path (Path): 游戏可执行文件的路径
            check_interval (int): 检查间隔时间（秒）
        """
        self.game_path = game_path
        self.game_name = game_path.name
        self.check_interval = check_interval
        self.is_running = False
        self.monitoring = False
        
    def is_game_running(self):
        """检查游戏进程是否正在运行"""
        try:
            # 获取进程名（不带扩展名）
            process_name = self.game_path.stem.lower()
            
            for process in psutil.process_iter(['name', 'exe']):
                try:
                    # 检查进程名或完整路径是否匹配
                    if (process.info['name'] and 
                        process.info['name'].lower().startswith(process_name)):
                        return True
                    if (process.info['exe'] and 
                        Path(process.info['exe']).resolve() == self.game_path.resolve()):
                        return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return False
        except Exception as e:
            print(f"检查进程时出错: {e}")
            return False
    
    def send_notification(self, title, message):
        """发送桌面通知"""
        try:
            notification.notify(
                title=title,
                message=message,
                timeout=10,  # 通知显示时间（秒）
                app_name="游戏监控器"
            )
            print(f"通知: {title} - {message}")
        except Exception as e:
            print(f"发送通知失败: {e}")
    
    def monitor_loop(self):
        """监控循环"""
        print(f"开始监控游戏: {self.game_name}")
        print(f"游戏路径: {self.game_path}")
        print("按 Ctrl+C 停止监控...")
        
        while self.monitoring:
            current_status = self.is_game_running()
            
            # 检测状态变化
            if current_status and not self.is_running:
                # 游戏启动
                self.send_notification(
                    "🎮 游戏已启动",
                    f"{self.game_name} 正在运行中"
                )
                self.is_running = True
                
            elif not current_status and self.is_running:
                # 游戏关闭
                self.send_notification(
                    "⏹️ 游戏已关闭",
                    f"{self.game_name} 已停止运行"
                )
                self.is_running = False
            
            time.sleep(self.check_interval)
    
    def start_monitoring(self):
        """开始监控"""
        if self.monitoring:
            print("监控已在运行中")
            return
            
        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self.monitor_loop)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
    
    def stop_monitoring(self):
        """停止监控"""
        self.monitoring = False
        if hasattr(self, 'monitor_thread'):
            self.monitor_thread.join(timeout=1)
        print("监控已停止")


def find_game_executable(search_paths: list[Path]) -> Optional[Path]:
    """在指定路径中查找游戏可执行文件"""
    target_patterns = [
        "DeltaForceClient.exe",
        "**/DeltaForceClient.exe",
        "DeltaForce/**/DeltaForceClient.exe"
    ]
    
    for search_path in search_paths:
        if not search_path.exists():
            continue
            
        for pattern in target_patterns:
            for candidate in search_path.glob(pattern):
                if candidate.is_file():
                    return candidate
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="监控Delta Force游戏运行状态。默认在WeGame安装目录中查找游戏。"
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path(r"C:\WeGameApps")],
        help="搜索游戏可执行文件的根目录（默认为 C:/WeGameApps）",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=3,
        help="检查间隔时间，单位秒（默认3秒）",
    )
    parser.add_argument(
        "--exe-path",
        type=Path,
        help="直接指定游戏可执行文件的完整路径（如果指定，则忽略搜索路径）",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    
    # 确定游戏可执行文件路径
    game_exe: Optional[Path] = None
    
    if args.exe_path:
        # 使用直接指定的路径
        game_exe = args.exe_path
        if not game_exe.exists():
            print(f"错误: 指定的游戏文件不存在: {game_exe}")
            return
    else:
        # 在搜索路径中查找游戏
        print(f"在以下路径中查找游戏: {args.paths}")
        game_exe = find_game_executable(args.paths)
        
        if not game_exe:
            print("未找到游戏可执行文件。")
            print("请使用以下方式之一指定游戏路径:")
            print("1. 命令行参数: python script.py \"C:\\GamePath\"")
            print("2. --exe-path 参数: python script.py --exe-path \"C:\\Game\\DeltaForceClient.exe\"")
            return
    
    print(f"找到游戏: {game_exe}")
    
    # 创建监控器实例
    monitor = GameMonitor(
        game_path=game_exe,
        check_interval=args.interval
    )
    
    try:
        # 启动监控
        monitor.start_monitoring()
        
        # 保持主线程运行
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n收到停止信号...")
    finally:
        monitor.stop_monitoring()


if __name__ == "__main__":
    main()