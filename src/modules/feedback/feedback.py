import json
import os
from pathlib import Path
import logging
from typing import List, Dict, Optional, Set, Union

from src.configs.config import OUTPUT_DIR
from src.modules.utils import save_result, load_file_as_string

logger = logging.getLogger(__name__)

class FeedbackManager:
    def __init__(self, task_id:str):
        self.task_id = task_id
        self.task_dir = Path(OUTPUT_DIR) / task_id
        self.feedback_dir = self.task_dir / "feedback"
        self.feedback_file = self.feedback_dir / "user_feedback.json"
        self.iteration_counter_file = self.feedback_dir / "iteration_counter.json"
        os.makedirs(self.feedback_dir, exist_ok=True)
        self.iteration = self._load_iteration_counter()
        self.feedback_data = self._load_feedback_data()

    def _load_iteration_counter(self) -> int:
        if not self.iteration_counter_file.exists():
            counter_data = {"iteration": 1}
            save_result(json.dumps(counter_data), self.iteration_counter_file)
            return 1
        
        try:
            counter_data = json.loads(load_file_as_string(self.iteration_counter_file))
            return counter_data.get("iteration", 1)
        except Exception as e:
            logger.error(f"加载迭代计数失败: {e}")
            return 1
        
    def _increment_iteration(self):
        self.iteration += 1
        counter_data = {"iteration": self.iteration}
        save_result(json.dumps(counter_data), self.iteration_counter_file)

    def _load_feedback_data(self) -> Dict:
        if not self.feedback_file.exists():
            feedback_data = {
                "iterations": {},
                "keywords_to_add": set(),
                "keywords_to_remove": set(),
                "papers_to_keep": set(),
                "papers_to_exclude": set(),
                "topics_to_emphasize": set(),
                "topics_to_reduce": set()
            }
            self._save_feedback_data(feedback_data)
            return feedback_data
        
        try:
            feedback_data = json.loads(load_file_as_string(self.feedback_file))
            for key in ["keywords_to_add", "keywords_to_remove", "papers_to_keep", 
                        "papers_to_exclude", "topics_to_emphasize", "topics_to_reduce"]:
                feedback_data[key] = set(feedback_data.get(key, []))
            return feedback_data
        except Exception as e:
            logger.error(f"加载反馈数据失败: {e}")
            return {
                "iterations": {},
                "keywords_to_add": set(),
                "keywords_to_remove": set(),
                "papers_to_keep": set(),
                "papers_to_exclude": set(),
                "topics_to_emphasize": set(),
                "topics_to_reduce": set()
            }
        
    def _save_feedback_data(self, data: Optional[Dict] = None):
        if data is None:
            data = self.feedback_data
        serializable_data = {
            "iterations": data["iterations"],
            "keywords_to_add": list(data["keywords_to_add"]),
            "keywords_to_remove": list(data["keywords_to_remove"]),
            "papers_to_keep": list(data["papers_to_keep"]),
            "papers_to_exclude": list(data["papers_to_exclude"]),
            "topics_to_emphasize": list(data["topics_to_emphasize"]),
            "topics_to_reduce": list(data["topics_to_reduce"])
        }
        save_result(json.dumps(serializable_data, indent=2), self.feedback_file)

    def save_current_iteration_papers(self, paper_ids: List[str]):
        self.feedback_data["iterations"][str(self.iteration)] = {
            "paper_ids": paper_ids
        }
        self._save_feedback_data()
    
    def add_user_feedback(self, 
                         keywords_to_add: Optional[List[str]] = None,
                         keywords_to_remove: Optional[List[str]] = None,
                         papers_to_keep: Optional[List[str]] = None,
                         papers_to_exclude: Optional[List[str]] = None,
                         topics_to_emphasize: Optional[List[str]] = None,
                         topics_to_reduce: Optional[List[str]] = None,
                         feedback_text: Optional[str] = None):
        if keywords_to_add:
            self.feedback_data["keywords_to_add"].update(keywords_to_add)
        if keywords_to_remove:
            self.feedback_data["keywords_to_remove"].update(keywords_to_remove)
        if papers_to_keep:
            self.feedback_data["papers_to_keep"].update(papers_to_keep)
        if papers_to_exclude:
            self.feedback_data["papers_to_exclude"].update(papers_to_exclude)
        if topics_to_emphasize:
            self.feedback_data["topics_to_emphasize"].update(topics_to_emphasize)
        if topics_to_reduce:
            self.feedback_data["topics_to_reduce"].update(topics_to_reduce)
        if feedback_text:
            if "iterations" not in self.feedback_data:
                self.feedback_data["iterations"] = {}
            if str(self.iteration) not in self.feedback_data["iterations"]:
                self.feedback_data["iterations"][str(self.iteration)] = {}
            
            self.feedback_data["iterations"][str(self.iteration)]["feedback_text"] = feedback_text
        
        self._save_feedback_data()

    def get_papers_to_exclude(self) -> Set[str]:
        return self.feedback_data["papers_to_exclude"]
    
    def get_papers_to_keep(self) -> Set[str]:
        return self.feedback_data["papers_to_keep"]
    
    def get_additional_keywords(self) -> Set[str]:
        return self.feedback_data["keywords_to_add"]
    
    def get_keywords_to_remove(self) -> Set[str]:
        return self.feedback_data["keywords_to_remove"]
    
    def get_emphasized_topics(self) -> Set[str]:
        return self.feedback_data["topics_to_emphasize"]
    
    def get_reduced_topics(self) -> Set[str]:
        return self.feedback_data["topics_to_reduce"]
    
    def prepare_for_next_iteration(self):
        self._increment_iteration()
        return self.iteration
    
    def get_all_previous_papers(self) -> Set[str]:
        all_papers = set()
        for iteration_data in self.feedback_data["iterations"].values():
            if "paper_ids" in iteration_data:
                all_papers.update(iteration_data["paper_ids"])
        return all_papers
