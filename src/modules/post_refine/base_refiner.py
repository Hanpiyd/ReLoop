from pathlib import Path
from typing import List, Union, Dict
import logging
from src.configs.utils import load_latest_task_id, ensure_task_dirs
from src.configs.config import OUTPUT_DIR, TASK_DIRS
from src.schema.paragraph import Paragraph
from src.LLM.ChatAgent import ChatAgent
from src.modules.utils import load_papers

logger = logging.getLogger(__name__)

class BaseRefiner:
    def __init__(self, task_id:str = None, **kwargs):
        task_id = load_latest_task_id() if task_id is None else task_id
        self.task_id = task_id
        
        self.task_dir = ensure_task_dirs(task_id)
        self.paper_dir = self.task_dir / TASK_DIRS["PAPERS_DIR"]
        self.tmp_dir = self.task_dir / TASK_DIRS["TMP_DIR"]
        self.latex_dir = self.task_dir / TASK_DIRS["LATEX_DIR"]
        
        if "papers" not in kwargs:
            self.papers = self.load_papers(self.paper_dir)
        else:
            self.papers = kwargs["papers"]
        if "chat_agent" in kwargs:
            self.chat_agent = kwargs["chat_agent"]
        else:
            self.chat_agent = ChatAgent()
            
    def load_papers(self, paper_dir_path_or_papers):
        return load_papers(paper_dir_path_or_papers=paper_dir_path_or_papers)
    
    def load_survey_sections(self, mainbody_path):
        paragraph_l = Paragraph.from_mainbody_path(mainbody_path=mainbody_path)
        return paragraph_l