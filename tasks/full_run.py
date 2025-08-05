import subprocess
import sys
from pathlib import Path
import logging

FILE_PATH = Path(__file__).absolute()
BASE_DIR = FILE_PATH.parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.configs.config import (
    BASE_DIR, 
    GENERATE_ONLY_RELATED_WORK
)
from src.models.generator.outlines_generator import OutlinesGenerator
from src.models.generator.content_generator import ContentGenerator
from src.models.generator.latex_generator import LatexGenerator
from src.LLM.ChatAgent import ChatAgent
from src.models.post_refine.post_refiner import PostRefiner
from src.modules.preprocessor.preprocessor import single_preprocessing
from src.modules.preprocessor.utils import parse_arguments_for_preprocessor

logger = logging.getLogger(__name__)

def check_latexmk_installed():
    try:
        _ = subprocess.run(['latexmk', '--version'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logger.debug("latexmk is installed.")
        return True
    except subprocess.CalledProcessError as e:
        logger.debug("latexmk is not installed.")
        return False
    except FileNotFoundError:
        logger.debug("latexmk is not installed.")
        return False
    
def generate_single_survey(task_id:str, chat_agent:ChatAgent=None):
    if chat_agent is None:
        chat_agent = ChatAgent()
 
    outline_generator = OutlinesGenerator(task_id)
    outline_generator.run()

    content_generator = ContentGenerator(task_id=task_id)
    content_generator.run()

    post_refiner = PostRefiner(task_id=task_id, chat_agent=chat_agent)
    post_refiner.run()

    latex_generator = LatexGenerator(task_id=task_id)
    if GENERATE_ONLY_RELATED_WORK:
        latex_generator.generate_related_work_only()
    else:
        latex_generator.generate_full_survey()
    if check_latexmk_installed():
        logger.info(f"Start compiling with latexmk.")
        latex_generator.compile_single_survey()
    else:
        logger.error(f"Compiling failed, as there is no latexmk installed in this machine.")
        
if __name__ == "__main__":
    args = parse_arguments_for_preprocessor()
    task_id = single_preprocessing(args)
    generate_single_survey(task_id=task_id)