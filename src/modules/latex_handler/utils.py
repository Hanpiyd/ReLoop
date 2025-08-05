from collections import defaultdict
import json
import os
import re
from typing import List, Tuple
from rapidfuzz import process
import logging

logger = logging.getLogger(__name__)


def load_all_papers(dir_path: str) -> list[dict]:
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


def fuzzy_match(text: str, candidates: list[str]) -> Tuple[str, int]:
    closest_text, score, idx = process.extractOne(text, candidates)
    return closest_text, idx