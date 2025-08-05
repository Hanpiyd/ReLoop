import sys
from pathlib import Path

FILE_PATH = Path(__file__).absolute()
BASE_DIR = FILE_PATH.parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.configs.config import GENERATE_ONLY_RELATED_WORK
from src.modules.preprocessor.preprocessor import single_preprocessing
from src.LLM.ChatAgent import ChatAgent
from src.modules.preprocessor.utils import parse_arguments_for_preprocessor
from src.models.generator.outlines_generator import OutlinesGenerator
from src.models.generator.content_generator import ContentGenerator
from src.models.post_refine.post_refiner import PostRefiner
from src.models.generator.latex_generator import LatexGenerator

if __name__ == "__main__":
    args = parse_arguments_for_preprocessor()
    task_id = single_preprocessing(args)
    og = OutlinesGenerator(task_id)
    og.run()
    content_generator = ContentGenerator(task_id)
    content_generator.run()
    post_refiner = PostRefiner(task_id)
    post_refiner.run()
    if not GENERATE_ONLY_RELATED_WORK:
        latex_generator = LatexGenerator(task_id)
        latex_generator.generate_full_survey()