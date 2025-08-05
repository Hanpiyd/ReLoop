import os
from pathlib import Path
import tiktoken
import logging

from src.configs.config import CUT_WORD_LENGTH

logger = logging.getLogger(__name__)

def load_prompt(file_path, **kwargs):
    if os.path.exists(file_path):
        with open(file_path, encoding="utf-8") as f:
            return f.read().format(**kwargs)
    else:
        logger.error(f"Prompt template not found at {file_path}")
        return ""
    
def num_token_from_string(text, model = "gpt-4o-mini"):
    encoding = tiktoken.encoding_for_model(model)
    encoded_text = encoding.encode(text)
    return len(encoded_text)

def cut_text_by_token(text, max_tokens, model = "gpt-4o-mini"):
    try:
        encoding = tiktoken.encoding_for_model(model)
        encoded_text = encoding.encode(text)
        cut_text = encoding.decode(encoded_text[:max_tokens])
    except Exception as e:
        logger.error(e)
        cut_text = text[: CUT_WORD_LENGTH * max_tokens]
    return cut_text