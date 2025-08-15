import os
import json
import logging
import re
import ast
from pathlib import Path
from collections import defaultdict
from typing import List, Tuple, Union, Dict
import logging

logger = logging.getLogger(__name__)

def shut_loggers():
    for logger in logging.Logger.manager.loggerDict:
        logging.getLogger(logger).setLevel(logging.INFO)
        
def sanitize_filename(filename: str) -> str:
    return re.sub(r'[\\/:"*?<>|]', "_", filename)

def save_result(result, path):
    if isinstance(path, str):
        path = Path(path)
    directory = path.parent
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(result)
        
def load_file_as_string(path: Union[str, Path]) -> str:
    if isinstance(path, str):
        with open(path, "r", encoding="utf-8") as fr:
            return fr.read()
    elif isinstance(path, Path):
        with path.open("r", encoding="utf-8") as fr:
            return fr.read()
    else:
        raise ValueError(path)
    
def update_config(dic: dict, config_path: str):
    config_path = Path(config_path)
    if config_path.exists():
        config: dict = json.load(open(config_path, "r", encoding="utf-8"))
        config.update(dic)
    else:
        config: dict = dic
    save_result(json.dumps(config, indent=4), config_path)
    
def save_as_json(result: dict, path: str):
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=4)
        
def load_meta_data(dir_path):
    data = []
    for filename in os.listdir(dir_path):
        if filename.endswith(".json"):
            file_path = os.path.join(dir_path, filename)
            with open(file_path, "r", encoding="utf-8") as file:
                result = json.load(file)
            data.append(result)
    return data


def load_single_file(file_path):
    if not os.path.exists(file_path):
        return ""
    with open(file_path, "r") as file:
        article = json.load(file)
    return article

def load_prompt(filename: str, **kwargs) -> str:
    path = os.path.join("", filename)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().format(**kwargs)
    else:
        logger.error(f"Prompt template not found at {path}")
        return ""
    
Clean_patten = re.compile(pattern=r"```(json|latex)?", flags=re.DOTALL)
def clean_chat_agent_format(content: str):
    content = re.sub(Clean_patten, "", content)
    return content

def load_papers(paper_dir_path_or_papers: Union[Path, List[Dict]]) -> list[dict]:
    if isinstance(paper_dir_path_or_papers, Path):
        papers = []
        for file in os.listdir(paper_dir_path_or_papers):
            file_path = paper_dir_path_or_papers / file
            if file_path.is_dir():
                file_path = file_path / os.listdir(file_path)[0]
            if not file_path.is_file():
                logger.error(f"loading paper error: {file_path} is not a file.")
                continue
            paper = json.loads(load_file_as_string(file_path))
            papers.append(paper)
        return papers
    elif isinstance(paper_dir_path_or_papers, list):
        return paper_dir_path_or_papers
    else:
        raise ValueError()
    
def load_file_as_text(file_path: Path):
    with file_path.open("r", encoding="utf-8") as fr:
        return fr.read()
    
def str2bool(v):
    if isinstance(v, bool):
        return v
    if v.lower() in ('yes', "true", "y", "t", "1"):
        return True
    elif v.lower() in ('no', 'false', "n", "f", "0"):
        return False