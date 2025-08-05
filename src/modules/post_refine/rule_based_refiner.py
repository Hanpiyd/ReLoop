import json
import os
import re
import random as normalrandom
from pathlib import Path
from typing import List, Union, Dict
from tqdm import tqdm
from llama_index.core import ChatPromptTemplate, PromptTemplate
import logging

from src.configs.config import(
    BASE_DIR,
    OUTPUT_DIR,
    RESOURCE_DIR,
    TASK_DIRS,
    MAINBODY_FILES
)
from src.configs.utils import load_latest_task_id, ensure_task_dirs
from src.modules.utils import load_file_as_string, save_result, load_prompt, clean_chat_agent_format
from src.schema.paragraph import Paragraph
from src.modules.post_refine.base_refiner import BaseRefiner
from src.modules.heuristic_modules.map_cited_bib_names_to_refs import BibNameReplacer
from src.modules.heuristic_modules.replace_abbr import AbbrReplacer

logger = logging.getLogger(__name__)

class RuleBasedRefiner(BaseRefiner):
    def __init__(self, task_id:str = None, **kwargs):
        super().__init__(task_id=task_id, **kwargs)
        
        self.task_dir = ensure_task_dirs(self.task_id)
        self.tmp_dir = self.task_dir / TASK_DIRS["TMP_DIR"]
        
        self.refined_mainbody_path = self.tmp_dir / MAINBODY_FILES["RULE"]
        
        self.abbr_replacer = AbbrReplacer()
        self.bib_name_replacer = BibNameReplacer(task_id=self.task_id)
        self.sp_phrases_to_be_rm = [
            "Certainly! Below is the rewritten content following your instructions:"
        ]
        self.re_pattern_rm_tokens_1 = re.compile(
            pattern=r"\*\*.*?\*\*", flags=re.DOTALL
        )
        self.re_pattern_rm_tokens_2 = re.compile(
            pattern=r"Certainly!.*?following your instructions:", flags=re.DOTALL
        )
        self.replace_rules = [
            self.bib_name_replacer.process,
            self.remove_unexpected_tokens,
        ]
        
    def rule_replace_pipeline(self, content:str, funcs:list):
        for func in funcs:
            content = func(content)
        return content
    
    def remove_unexpected_tokens(self, content:str):
        for one in self.sp_phrases_to_be_rm:
            content = content.replace(one, "")
        content = re.sub(self.re_pattern_rm_tokens_1, "", content)
        content = re.sub(self.re_pattern_rm_tokens_2, "", content)
        return clean_chat_agent_format(content)
    
    def find_differences(self, str1, str2):
        sents1 = str1.strip().split(".")
        sents2 = str2.strip().split(".")
        differences = []
        for i, (sent1, sent2) in enumerate(zip(sents1, sents2)):
            if sent1 != sent2:
                differences.append(
                    (
                        i,
                        sent1.strip().replace("\n\n", ""),
                        sent2.strip().replace("\n\n", ""),
                    )
                )
                
        return differences
    
    
    def show_differences(self, differences, str1_label="revised_content", str2_label="sec_content"):
        for index, sent1, sent2 in differences:
            logger.info(
                f"Position {index}: {str1_label} has '{sent1}'|||||| {str2_label} has '{sent2}'"
            )
            
    def run(self, mainbody_path = None):
        if mainbody_path is None:
            mainbody_path = self.tmp_dir / MAINBODY_FILES["REWRITTEN"]
            
        survey_sections = self.load_survey_sections(mainbody_path)
        revised_content_list = []
        for sec in tqdm(survey_sections, desc="Rule based refining..."):
            revised_content = self.rule_replace_pipeline(content=sec.content, funcs=self.replace_rules)
            revised_content_list.append(revised_content)
            
        revised_survey_text = "\n".join(revised_content_list)
        save_result(revised_survey_text, self.refined_mainbody_path)
        logger.debug(f"Save content to {self.refined_mainbody_path}.")
        return revised_survey_text