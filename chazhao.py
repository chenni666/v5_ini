import argparse
import string
from pathlib import Path
from typing import Iterable, List


TARGET_SUFFIX = Path("DeltaForce") / "Saved" / "Config" / "WindowsClient" / "GameUserSettings.ini"
DEFAULT_ROOTS = [Path("C:/WeGameApps"), Path("D:/WeGameApps")]


def find_game_configs(root_dirs: Iterable[Path]) -> List[Path]:
    """Search the supplied roots for GameUserSettings.ini files under the expected Delta Force path."""
    matches: List[Path] = []

    for root in root_dirs:
        if not root.exists():
            continue

        for candidate in root.rglob("GameUserSettings.ini"):
            if candidate.is_file():
                # 检查路径是否包含 DeltaForce 目录结构
                candidate_parts = candidate.parts
                target_parts = TARGET_SUFFIX.parts
                
                # 检查路径末尾是否匹配目标结构
                if len(candidate_parts) >= len(target_parts):
                    if candidate_parts[-len(target_parts):] == target_parts:
                        matches.append(candidate)

    return matches


def list_drive_roots() -> List[Path]:
    """Enumerate existing drive roots (e.g. C:\\, D:\\)."""
    roots = []

    for letter in string.ascii_uppercase:
        drive_root = Path(f"{letter}:\\")
        if drive_root.exists():
            roots.append(drive_root)

    return roots


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Locate Delta Force configuration files. "
            "Defaults to scanning the WeGame installation directory if no paths are supplied."
        )
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=DEFAULT_ROOTS,
        help="Root directories to search (defaults to C:/WeGameApps and D:/WeGameApps).",
    )
    parser.add_argument(
        "--first",
        action="store_true",
        help="Stop after finding the first matching configuration file.",
    )
    parser.add_argument(
        "--global-search",
        action="store_true",
        help="Scan every available drive letter (may be slow).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    roots = list(dict.fromkeys(args.paths))  # remove duplicates while preserving order

    if args.global_search:
        roots = list_drive_roots()

    matches = find_game_configs(roots)

    if not matches and not args.global_search and roots == DEFAULT_ROOTS:
        print("未找到配置文件，开始进行全盘扫描，这可能需要一些时间……")
        matches = find_game_configs(list_drive_roots())

    if not matches:
        print("No matching GameUserSettings.ini files were found.")
        return

    if args.first:
        print(matches[0])
        return

    for match in matches:
        print(match)


if __name__ == "__main__":
    main()