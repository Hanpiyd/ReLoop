import os
import re
from datetime import datetime
from pathlib import Path

from src.configs.config import OUTPUT_DIR, TASK_DIRS

def load_task_id_by_date(target_dir: Path = None):
    pattern = re.compile(r"(\d{4}-\d{2}-\d{2}-\d{4})_.+")

    matched_directories = []
    if target_dir is None:
        target_dir = Path(f"{OUTPUT_DIR}")
    for dir_name in os.listdir(target_dir):
        match = pattern.match(dir_name)
        if match:
            timestamp_str = match.group(1)
            try:
                timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d-%H%M")
                if not target_dir.joinpath(dir_name).is_dir():
                    continue
                matched_directories.append((dir_name, timestamp))
            except ValueError:
                print(f"Invalid date format: {timestamp_str}")

    matched_directories.sort(key=lambda x: x[1], reverse=True)
    return [name for name, _ in matched_directories]

def load_latest_task_id(target_dir: Path = None):
    if target_dir is None:
        target_dir = Path(f"{OUTPUT_DIR}")
    task_ids = load_task_id_by_date(target_dir=target_dir)
    return task_ids[0] if len(task_ids) else None

def ensure_task_dirs(task_id: str):
    task_base_dir = OUTPUT_DIR / task_id
    task_base_dir.mkdir(parents=True, exist_ok=True)
    
    for dir_name in TASK_DIRS.values():
        dir_path = task_base_dir / dir_name
        dir_path.mkdir(parents=True, exist_ok=True)
    
    return task_base_dir