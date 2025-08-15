import re
from tqdm import tqdm
import traceback
from pathlib import Path
import logging

from src.configs.config import(
    OUTPUT_DIR,
    RESOURCE_DIR,
    ADVANCED_CHATAGENT_MODEL,
    TASK_DIRS,
    MAINBODY_FILES
)

from src.configs.utils import load_latest_task_id, ensure_task_dirs
from src.modules.utils import save_result, load_prompt, clean_chat_agent_format
from src.modules.post_refine.base_refiner import BaseRefiner

logger = logging.getLogger(__name__)

def escape_latex(string):
    if not string:
        return string
        
    placeholders = {}
    
    latex_pattern = re.compile(r'(\\[a-zA-Z]+(?:\[[^\]]*\])?(?:\{[^}]*\})*|\\begin\{[^}]+\}.*?\\end\{[^}]+\})', re.DOTALL)
    matches = list(latex_pattern.finditer(string))
    
    for i, match in enumerate(matches):
        placeholder = f"__LATEX_PLACEHOLDER_{i}__"
        placeholders[placeholder] = match.group(0)
        string = string[:match.start()] + placeholder + string[match.end():]
        
    replacements = {
        "{": "\\{",
        "}": "\\}",
        "%": "\\%",
        "#": "\\#",
        "$": "\\$",
        "_": "\\_",
        "&": "\\&",
        "^": "\\textasciicircum{}",
        "~": "\\textasciitilde{}",
        "\\": "\\textbackslash{}",
    }
        
    for original, escape in replacements.items():
        string = string.replace(original, escape)
    
    for placeholder, original in placeholders.items():
        string = string.replace(placeholder, original)
    
    return string

def process_llm_content_for_latex(content, preserve_environments=True):
    if not content:
        return content
    
    latex_indicators = [r'\\section', r'\\subsection', r'\\label', r'\\cite', r'\\begin', r'\\end']
    is_latex = any(re.search(indicator, content) for indicator in latex_indicators)
    
    if is_latex:
        placeholders = {}
        placeholder_counter = 0
        
        env_pattern = re.compile(r'\\begin\{([^}]+)\}(.*?)\\end\{\1\}', re.DOTALL)
        for match in env_pattern.finditer(content):
            placeholder = f"__LATEX_ENV_{placeholder_counter}__"
            placeholders[placeholder] = match.group(0)
            content = content.replace(match.group(0), placeholder)
            placeholder_counter += 1
        
        cmd_pattern = re.compile(r'\\[a-zA-Z]+(?:\[[^\]]*\])?(?:\{[^}]*\})*')
        for match in cmd_pattern.finditer(content):
            placeholder = f"__LATEX_CMD_{placeholder_counter}__"
            placeholders[placeholder] = match.group(0)
            content = content.replace(match.group(0), placeholder)
            placeholder_counter += 1
        
        percent_pattern = re.compile(r'(\d+(?:\.\d+)?)\s*%')
        content = percent_pattern.sub(r'\1\\%', content)
        
        for placeholder, original in placeholders.items():
            content = content.replace(placeholder, original)
        
        content = re.sub(r'\n{3,}', '\n\n', content)
        content = re.sub(r'\\textbackslash\{\}', '\\\\', content)
        content = re.sub(r'(\n\n\\par\n\n)+', '\n\n', content)
        content = re.sub(r'(^|[^\n])\\par\n(?=[^\n])', r'\1\n\n', content)
        
        return content
    
    if preserve_environments:
        placeholders = {}
        placeholder_counter = 0
        
        env_pattern = re.compile(r'\\begin\{([^}]+)\}(.*?)\\end\{\1\}', re.DOTALL)
        for match in env_pattern.finditer(content):
            placeholder = f"__LATEX_ENV_{placeholder_counter}__"
            placeholders[placeholder] = match.group(0)
            content = content.replace(match.group(0), placeholder)
            placeholder_counter += 1
        
        cmd_pattern = re.compile(r'\\[a-zA-Z]+(?:\[[^\]]*\])?(?:\{[^}]*\})*')
        for match in cmd_pattern.finditer(content):
            placeholder = f"__LATEX_CMD_{placeholder_counter}__"
            placeholders[placeholder] = match.group(0)
            content = content.replace(match.group(0), placeholder)
            placeholder_counter += 1
    
    replacements = [
        ("\\", "\\textbackslash{}"), 
        ("{", "\\{"),
        ("}", "\\}"),
        ("%", "\\%"),
        ("#", "\\#"),
        ("$", "\\$"),
        ("_", "\\_"),
        ("&", "\\&"),
        ("^", "\\textasciicircum{}"),
        ("~", "\\textasciitilde{}"),
    ]
    
    for original, escape in replacements:
        parts = re.split(r'(__LATEX_(?:ENV|CMD)_\d+__)', content)
        new_parts = []
        for part in parts:
            if not part.startswith('__LATEX_'):
                part = part.replace(original, escape)
            new_parts.append(part)
        content = ''.join(new_parts)
    
    if preserve_environments:
        for placeholder, original in placeholders.items():
            content = content.replace(placeholder, original)
    
    inline_code_pattern = re.compile(r'`([^`]+)`')
    content = inline_code_pattern.sub(r'\\texttt{\1}', content)
    
    return content

class SectionRewriter(BaseRefiner):
    def __init__(self, task_id = None, **kwargs):
        super().__init__(task_id, **kwargs)
        
        self.task_dir = ensure_task_dirs(self.task_id)
        self.tmp_dir = self.task_dir / TASK_DIRS["TMP_DIR"]
        
        self.refined_mainbody_path = self.tmp_dir / MAINBODY_FILES["REWRITTEN"]
        
        self.rewrite_prompt_dir = Path(f"{RESOURCE_DIR}/LLM/prompts/section_rewriter")
        self.rewrite_project_prompt_dir = Path(f"{RESOURCE_DIR}/LLM/prompts/section_rewriter_project")
        self.maintain_commands = ["section", "subsection", "label", "autoref", "cite"]  # 添加 cite
        
    def extrace_environment_content(self, content, command):
        pattern = re.compile(r"\\" + command + r"\{([^}]+)\}")
        res = pattern.findall(content)
        return res if res is not None else []
    
    def replace_environment_contents(self, rewritten_text, original_text, commands):
        for command in commands:
            original_contents = self.extrace_environment_content(original_text, command)
            current_contents = self.extrace_environment_content(rewritten_text, command)
            if len(original_contents) != len(current_contents):
                logger.error(
                    f"Rewriting failed. original_contents: {original_contents}; current_contents: {current_contents}"
                )
                continue
            
            for i, content in enumerate(current_contents):
                if content != original_contents[i]:
                    rewritten_text = rewritten_text.replace(
                        f"\\{command}{{{content}}}",
                        f"\\{command}{{{original_contents[i]}}}",
                    )
                    
        return rewritten_text
    
    def extract_section_line(self, latex_text):
        match = re.search(r"\\section\{([^}]*)\}", latex_text)
        if match:
            section_title = match.group(0)
            return section_title
        return None
    
    def rule_address_gpt_rewrite_issues(self, origin_sec_contents, new_sec_contents):
        if len(origin_sec_contents) != len(new_sec_contents):
            logger.error(
                f"Section count mismatch - origin: {len(origin_sec_contents)}, new: {len(new_sec_contents)}"
            )
            
        for idx in range(min(len(origin_sec_contents), len(new_sec_contents))):
            if "\\section" in origin_sec_contents[idx] and "\\section" not in new_sec_contents[idx]:
                origin_sec_head = origin_sec_contents[idx].strip()[:200]
                sec_line = self.extract_section_line(latex_text=origin_sec_head)
                if sec_line is None:
                    logger.warning(f"Could not extract section line from: {origin_sec_head}")
                    continue
                new_sec_contents[idx] = f"{sec_line}\n\n{new_sec_contents[idx]}"
                logger.debug(f'"{sec_line}" is inserted into original section')
                
                potential_subsec_line = sec_line.replace("\\section", "\\subsection")
                if potential_subsec_line in new_sec_contents[idx]:
                    logger.debug(
                        f"{potential_subsec_line} was added in rewritten section by mistake, correcting..."
                    )
                    new_sec_contents[idx] = new_sec_contents[idx].replace(
                        potential_subsec_line, ""
                    )
        
        for idx in range(min(len(origin_sec_contents), len(new_sec_contents))):
            new_sec_contents[idx] = self.replace_environment_contents(
                rewritten_text=new_sec_contents[idx],
                original_text=origin_sec_contents[idx],
                commands=self.maintain_commands,
            )
            
    def check_and_remove_extra_conclusion(self, content):
        conclusion_pattern = re.compile(r'\\section\{Conclusion\}.*?(?=\\section|$)', re.DOTALL | re.IGNORECASE)
        matches = list(conclusion_pattern.finditer(content))
        
        if len(matches) > 1:
            logger.warning(f"Found {len(matches)} conclusion sections, keeping only the last one")
            for match in matches[:-1]:
                content = content[:match.start()] + content[match.end():]
        
        return content
            
    def compress_sections(self, origin_sec_contents, GENERATE_RELATED_WORK_ONLY:bool = False, GENERATE_PROPOSAL:bool = False):
        sec_prompts = []
        origin_lengths = []
        for sec_content in origin_sec_contents:
            if not GENERATE_PROPOSAL:
                sec_rewrite_prompt = load_prompt(
                    filename=str(
                        self.rewrite_prompt_dir.joinpath("compress_sections.md").absolute()
                    ),
                    content=sec_content,
                )
            else:
                sec_rewrite_prompt = load_prompt(
                    filename=str(
                        self.rewrite_project_prompt_dir.joinpath("compress_sections.md").absolute()
                    ),
                    content=sec_content,
                )
            origin_lengths.append(len(sec_content.strip().split()))
            sec_prompts.append(sec_rewrite_prompt)
            
        section_content_list = self.chat_agent.batch_remote_chat(
            sec_prompts, desc="compress sections..."
        )
        
        new_sec_contents = [
            clean_chat_agent_format(content=one) for one in section_content_list
        ]
        
        new_sec_contents_processed = []
        for content in new_sec_contents:
            new_sec_contents_processed.append(process_llm_content_for_latex(content))
        new_sec_contents = new_sec_contents_processed
        
        self.rule_address_gpt_rewrite_issues(
            origin_sec_contents=origin_sec_contents, new_sec_contents=new_sec_contents
        )
        
        current_lengths = [len(one.strip().split()) for one in new_sec_contents]
        for id_, (ori, cur) in enumerate(zip(origin_lengths, current_lengths)):
            logger.info(
                f"Section {id_ + 1}: original={ori} words, compressed={cur} words, ratio={round(cur / ori, 2)}"
            )

        return new_sec_contents
    
    def rewrite_main_sections(self, origin_sec_contents, GENERATE_RELATED_WORK_ONLY:bool = False, GENERATE_PROPOSAL:bool = False):
        new_sec_contents = []
        if not origin_sec_contents:
            logger.warning("No sections found to rewrite")
            return []

        if not (GENERATE_RELATED_WORK_ONLY or GENERATE_PROPOSAL):
            introduction_content = origin_sec_contents[0]
            intro_compression_prompt = load_prompt(
                filename=str(
                    self.rewrite_prompt_dir.joinpath(
                        "introduction_compression.md"
                    ).absolute()
                ),
                introduction_sec=introduction_content
            )
            compressed_intro = self.chat_agent.remote_chat(
                intro_compression_prompt, model=ADVANCED_CHATAGENT_MODEL
            )
            compressed_intro = clean_chat_agent_format(compressed_intro)
            compressed_intro = process_llm_content_for_latex(compressed_intro)
            new_sec_contents.append(compressed_intro)
        else:
            compressed_intro = origin_sec_contents[0]
            new_sec_contents.append(origin_sec_contents[0])

        compressed_context = compressed_intro
        sec_id = 2
        for sec_content in tqdm(origin_sec_contents[1:], desc=f"Rewriting sections"):
            if not GENERATE_PROPOSAL:
                sec_rewrite_prompt = load_prompt(
                    filename=str(
                        self.rewrite_prompt_dir.joinpath(
                            "rewrite_section_with_compressed_context.md"
                        ).absolute()
                    ),
                    context=compressed_context,
                    content=sec_content,
                )
            else:
                sec_rewrite_prompt = load_prompt(
                    filename=str(
                        self.rewrite_project_prompt_dir.joinpath(
                            "rewrite_section_with_compressed_context.md"
                        ).absolute()
                    ),
                    context=compressed_context,
                    content=sec_content,
                )
            new_sec_content = self.chat_agent.remote_chat(
                sec_rewrite_prompt, model=ADVANCED_CHATAGENT_MODEL
            )
            new_sec_content = clean_chat_agent_format(new_sec_content)
            new_sec_content = process_llm_content_for_latex(new_sec_content)
            new_sec_contents.append(new_sec_content)
            sec_id += 1
            
            if len(new_sec_contents) >= len(origin_sec_contents):
                break
                
            if not GENERATE_PROPOSAL:    
                compression_prompt = load_prompt(
                    filename=str(
                        self.rewrite_prompt_dir.joinpath(
                            "iterative_compression.md"
                        ).absolute()
                    ),
                    previous_compression=compressed_context,
                    content_for_compressions=sec_content,
                )
            else:
                compression_prompt = load_prompt(
                    filename=str(
                        self.rewrite_project_prompt_dir.joinpath(
                            "iterative_compression.md"
                        ).absolute()
                    ),
                    previous_compression=compressed_context,
                    content_for_compressions=sec_content,
                )
            compressed_context = self.chat_agent.remote_chat(
                compression_prompt, model=ADVANCED_CHATAGENT_MODEL
            )
            compressed_context = clean_chat_agent_format(compressed_context)
            compressed_context = process_llm_content_for_latex(compressed_context)
            
        self.rule_address_gpt_rewrite_issues(
            origin_sec_contents=origin_sec_contents, new_sec_contents=new_sec_contents
        )
        
        origin_lengths = [len(one.strip().split()) for one in origin_sec_contents]
        current_lengths = [len(one.strip().split()) for one in new_sec_contents]
        for id_, (ori, cur) in enumerate(zip(origin_lengths, current_lengths)):
            logger.info(
                f"Section {id_ + 1}: original={ori} words, rewritten={cur} words, ratio={round(cur / ori, 2)}"
            )
            
        return new_sec_contents
    
    def rewrite_conclusion(self, origin_conclusion_text, introduction_text, GENERATE_RELATED_WORK_ONLY:bool = False, GENERATE_PROPOSAL:bool = False):
        if GENERATE_RELATED_WORK_ONLY:
            return origin_conclusion_text
        else:
            prompt = load_prompt(
                filename=str(
                    self.rewrite_prompt_dir.joinpath("rewrite_conclusion.md").absolute()
                ),
                introduction=introduction_text,
                origin_conclusion=origin_conclusion_text,
            )
            conclusion = self.chat_agent.remote_chat(prompt, model=ADVANCED_CHATAGENT_MODEL)
            conclusion = clean_chat_agent_format(conclusion)
            conclusion = process_llm_content_for_latex(conclusion)
            
            origin_conclusion_tmp_list = [origin_conclusion_text]
            new_conclusion_temp_list = [conclusion]
            self.rule_address_gpt_rewrite_issues(
                origin_sec_contents=origin_conclusion_tmp_list,
                new_sec_contents=new_conclusion_temp_list
            )
            logger.info(f"Rewrote the conclusion section.")
            conclusion = new_conclusion_temp_list[0]
            return conclusion
    
    def run(self, mainbody_path = None, GENERATE_RELATED_WORK_ONLY:bool = False, GENERATE_PROPOSAL:bool = False):
        if mainbody_path is None:
            mainbody_path = self.tmp_dir / MAINBODY_FILES["RAG"]
            
        survey_sections = self.load_survey_sections(mainbody_path)
        sec_contents = [one.content for one in survey_sections]
        
        try:
            compressed_survey_sections = self.compress_sections(
                origin_sec_contents=sec_contents,
                GENERATE_RELATED_WORK_ONLY=GENERATE_RELATED_WORK_ONLY,
                GENERATE_PROPOSAL=GENERATE_PROPOSAL
            )
        except Exception as e:
            tb_str = traceback.format_exc()
            logger.error(
                f"Compression failed. Error: {e}\nTraceback: {tb_str}"
            )
            compressed_survey_sections = sec_contents
            
        try:
            rewritten_survey_sections = self.rewrite_main_sections(
                origin_sec_contents=compressed_survey_sections[:-1],
                GENERATE_RELATED_WORK_ONLY=GENERATE_RELATED_WORK_ONLY,
                GENERATE_PROPOSAL=GENERATE_PROPOSAL
            )    
        except Exception as e:
            tb_str = traceback.format_exc()
            logger.error(
                f"Rewriting failed. Error: {e}\nTraceback: {tb_str}"
            )
            rewritten_survey_sections = compressed_survey_sections[:-1]
            
        if not GENERATE_PROPOSAL:
            new_conclusion = self.rewrite_conclusion(
                origin_conclusion_text=compressed_survey_sections[-1],
                introduction_text=compressed_survey_sections[0],
                GENERATE_RELATED_WORK_ONLY=GENERATE_RELATED_WORK_ONLY,
                GENERATE_PROPOSAL=GENERATE_PROPOSAL
            
            )
            rewritten_survey_sections.append(new_conclusion)
        else:
            rewritten_survey_sections.append(compressed_survey_sections[-1])
        
        cleaned_sections = []
        for i, section in enumerate(rewritten_survey_sections):
            section = re.sub(r'\\par\s*\n', '\n\n', section)
            section = re.sub(r'\n\s*\\par\s*\n', '\n\n', section)
            section = re.sub(r'\\textbackslash\{\}par', '', section)
            section = re.sub(r'\n{3,}', '\n\n', section)
            cleaned_sections.append(section.strip())
        
        rewritten_survey_text = "\n\n".join(cleaned_sections)
        
        rewritten_survey_text = self.check_and_remove_extra_conclusion(rewritten_survey_text)
        
        save_result(rewritten_survey_text, self.refined_mainbody_path)
        logger.debug(f"Saved content to {self.refined_mainbody_path}")
        return rewritten_survey_text