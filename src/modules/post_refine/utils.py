from typing import List
import re

def are_key_words_contained(content: str, key_words: List[str] = []):
    for one in key_words:
        if one.strip().lower() in content.strip().lower():
            return True
    return False

def list_citation_names(content: str):
    pattern = r"\\cite[t|p]?{([^}]+)}"
    citations = re.findall(pattern, content)
    return citations