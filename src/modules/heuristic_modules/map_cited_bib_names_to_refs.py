import re
from pathlib import Path
import logging
from rapidfuzz import process

from src.configs.utils import load_latest_task_id
from src.configs.config import OUTPUT_DIR
from src.modules.utils import save_result, load_file_as_string

logger = logging.getLogger(__name__)

class BibNameReplacer(object):
    def __init__(self, task_id:str = None):
        self.ref_bibs = None
        self.task_id = task_id
        self.pattern_of_bib_name_in_paper = r"\\cite[t|p]*\{(.*?)\}"
        self.pattern_of_bib_name_in_references = (
            r"@[\w\-]+\{([^,]+)," 
        )
        self.ref_file_path = Path(f"{OUTPUT_DIR}/{task_id}/latex/references.bib")
        self.collect_ref_bibs()
        
    def collect_ref_bibs(self):
        ref_content = load_file_as_string(path=self.ref_file_path)
        bib_names = re.findall(
            pattern=self.pattern_of_bib_name_in_references, string=ref_content
        )
        self.ref_bibs = bib_names
        
    def process(self, content:str):
        bibs_in_content = re.findall(
            pattern=self.pattern_of_bib_name_in_paper, string=content
        )
        bibs_in_content = set(bibs_in_content)
        for bib_name_content in bibs_in_content:
            bib_names = [one.strip() for one in bib_name_content.split(",")]
            for bib_name in bib_names:
                closet_ref_bib_name, _, _ = process.extractOne(bib_name, self.ref_bibs)
                if closet_ref_bib_name != bib_name:
                    logger.error(
                        f"There is no {bib_name} in reference.bib; It has been replaced to {closet_ref_bib_name}"
                    )
                    content = content.replace(bib_name, closet_ref_bib_name)
                    
        return content
                