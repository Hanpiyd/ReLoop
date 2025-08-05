import argparse
import json
from pathlib import Path
from src.configs.config import OUTPUT_DIR, TASK_DIRS
from src.modules.utils import load_file_as_string
from src.configs.utils import ensure_task_dirs

class Base:
    def __init__(self, task_id: str | None = None):
        self.task_id = task_id
        self.task_dir = ensure_task_dirs(task_id)
        self.jsons_dir = self.task_dir / TASK_DIRS["JSONS_DIR"]
        self.papers_dir = self.task_dir / TASK_DIRS["PAPERS_DIR"]
        self.latex_dir = self.task_dir / TASK_DIRS["LATEX_DIR"]
        self.tmp_dir = self.task_dir / TASK_DIRS["TMP_DIR"]
        tmp_config = Base.load_tmp_config(task_id)
        self.title = tmp_config["title"]
        self.key_words = tmp_config["key_words"]
        self.topic = tmp_config["topic"]
        
    @staticmethod
    def load_tmp_config(task_id:str):
        path = Path(OUTPUT_DIR) / task_id / "tmp_config.json"
        dic = json.loads(load_file_as_string(path))
        missing_keys = [
            key for key in dic if key not in ["task_id", "title", "key_words", "topic"]
        ]
        return dic