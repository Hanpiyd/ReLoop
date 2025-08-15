import io
import os
import subprocess
import sys
import traceback
from pathlib import Path
import logging
import fitz

FILE_PATH = Path(__file__).absolute()
BASE_DIR = FILE_PATH.parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.configs.config import (
    OUTPUT_DIR, 
    RESOURCE_DIR, 
    TASK_DIRS, 
    MAINBODY_FILES,
    RELATED_WORK_SECTION_TITLE
)
from src.configs.utils import load_latest_task_id, ensure_task_dirs
from src.LLM.ChatAgent import ChatAgent
from src.modules.latex_handler.latex_text_builder import LatexTextBuilder

logger = logging.getLogger(__name__)

class LatexGenerator:
    def __init__(self, task_id:str, **kwargs):
        task_id = load_latest_task_id() if task_id is None else task_id
        self.task_id = task_id

        self.task_dir = ensure_task_dirs(task_id)
        self.tmp_dir = self.task_dir / TASK_DIRS["TMP_DIR"]
        self.latex_dir = self.task_dir / TASK_DIRS["LATEX_DIR"]
        
        self.outlines_path = self.task_dir / "outlines.json"
        self.init_text_tex_path = Path(f"{RESOURCE_DIR}/latex/survey.ini.tex")
        self.mainbody_tex_path = self.tmp_dir / MAINBODY_FILES["INITIAL"]
            
        self.post_refined_mainbody_tex_path = self.tmp_dir / MAINBODY_FILES["FINAL"]
        
        self.abstract_path = self.tmp_dir / "abstract.tex"
        self.survey_tex_path = self.latex_dir / "survey.tex"
        self.text_builder = LatexTextBuilder(self.init_text_tex_path)
        
    def add_watermark(self, input_pdf:Path, output_pdf:Path, watermark_pdf:Path):
        doc = fitz.open(input_pdf)
        watermark = fitz.open(watermark_pdf)
        watermark_page = watermark[0]
        watermark_pixmap = watermark_page.get_pixmap()
        img_stream = io.BytesIO(watermark_pixmap.tobytes())
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            page_rect = page.rect
            page.insert_image(page_rect, stream=img_stream, overlay=False, alpha=0.3)
        doc.save(output_pdf)
        
    def compile_single_survey(self):
        sty_file_path = Path(BASE_DIR) / "resources" / "latex" / "neurips_2024.sty"
        water_mark_pdf_path = Path(BASE_DIR) / "resources" / "latex" / "watermark.png"
        
        os.chdir(self.task_dir)
        if self.task_dir.joinpath("survey.pdf").exists():
            subprocess.run(f"rm survey.pdf", shell=True)
            subprocess.run(f"rm survey_wtmk.pdf", shell=True)
            
        os.chdir(self.latex_dir)
        subprocess.run(["cp", sty_file_path, "./neurips_2024.sty"])
        with open("compile.log", "w") as output_file:
            logger.debug(
                f'Running "latexmk -pdf -interaction=nonstopmode -f survey.tex". The compile.log is at {self.latex_dir / "compile.log"}'
            )
            subprocess.run(
                "latexmk -pdf -interaction=nonstopmode -f survey.tex",
                shell=True,
                stdout=output_file,
                stderr=output_file,
            )
            
        with open("compile.log", "a") as output_file:
            logger.debug(f'Running "latexmk -c"')
            subprocess.run("latexmk -c", shell=True, stdout=output_file)
            
        subprocess.run("rm *.bbl", shell=True)

        subprocess.run("rm neurips_2024.sty", shell=True)
        
        subprocess.run("mv survey.pdf ../", shell=True)
        self.add_watermark(
            self.task_dir / "survey.pdf", self.task_dir / "survey_wtmk.pdf", water_mark_pdf_path
        )
        
    def generate_full_survey(self):
        tex_content = self.text_builder.run(
            outlines_path=self.outlines_path,
            abstract_path=self.abstract_path,
            mainbody_path=self.post_refined_mainbody_tex_path,
            latex_save_path=self.survey_tex_path,
        )
        return tex_content
        
    def generate_related_work_only(self):
        from src.schema.outlines import Outlines
        
        outlines = Outlines.from_saved(self.outlines_path)
        
        self.text_builder.make_title(f"{outlines.title} - {RELATED_WORK_SECTION_TITLE}")
   
        mainbody = self.text_builder.load_file_as_string(self.post_refined_mainbody_tex_path)
        self.text_builder.make_content(mainbody)
 
        self.text_builder.make_reference()

        self.text_builder.make_disclaimer()

        self.text_builder.tex += "\n \\end{document} \n"

        self.text_builder.save_result(self.text_builder.tex, self.survey_tex_path)
        return self.text_builder.tex