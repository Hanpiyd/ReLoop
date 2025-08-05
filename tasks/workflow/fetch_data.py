import sys
from pathlib import Path

FILE_PATH = Path(__file__).absolute()
BASE_DIR = FILE_PATH.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.modules.preprocessor.preprocessor import single_preprocessing
from src.LLM.ChatAgent import ChatAgent
from src.modules.preprocessor.utils import parse_arguments_for_preprocessor

if __name__ == "__main__":
    args = parse_arguments_for_preprocessor()
    single_preprocessing(args)