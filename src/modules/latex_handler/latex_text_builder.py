import os
from pathlib import Path
from typing import Union
import logging

from src.configs.config import BASE_DIR
from src.schema.outlines import Outlines
from src.modules.utils import load_file_as_string, save_result

logger = logging.getLogger(__name__)

class LatexTextBuilder:
    def __init__(self, init_tex_path:str):
        self.tex = load_file_as_string(init_tex_path)
        
    def make_title(self, title:str, author="PaperMate"):
        self.tex += f"""\n\\title{{{title}}}\n\\author{{{author}}}\n\
        
\\begin{{document}}\n\\maketitle\n"""

    def make_abstract(self, abstract:str):
        self.tex += f"\n \n{abstract}\n"
        
    def make_content(self, mainbody):
        self.tex += "\n" + mainbody
        
    def make_reference(self):
        self.tex += """\\newpage
    
    

\\bibliography{references}
\\bibliographystyle{unsrtnat}

\\vfill"""


    def make_disclaimer(self):
        self.tex += """\\newpage
\\textbf{Disclaimer:}

Test.
"""

    def run(self, outlines_path, abstract_path, mainbody_path, latex_save_path):
        outlines = Outlines.from_saved(outlines_path)
        self.make_title(outlines.title)
        abstract = load_file_as_string(abstract_path)
        self.make_abstract(abstract)
        mainbody = load_file_as_string(mainbody_path)
        self.make_content(mainbody)
        self.make_reference()
        self.make_disclaimer()
        self.tex += "\n \\end{document} \n"
        save_result(self.tex, latex_save_path)
        return self.tex