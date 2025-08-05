import sys
from pathlib import Path

FILE_PATH = Path(__file__).absolute()
BASE_DIR = FILE_PATH.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.models.generator.latex_generator import LatexGenerator
from src.modules.preprocessor.utils import parse_arguments_for_integration_test

if __name__ == "__main__":
    task_id = parse_arguments_for_integration_test()
    latex_generator = LatexGenerator(task_id)  
    latex_generator.generate_full_survey()
    # latex_generator.compile_single_survey()