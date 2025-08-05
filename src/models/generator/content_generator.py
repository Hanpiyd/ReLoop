import json
import os
import re
import sys
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.client import responses
from pathlib import Path
import matplotlib.pyplot as plt
from tenacity import retry, stop_after_attempt, wait_fixed
from tqdm import tqdm
import logging

FILE_PATH = Path(__file__).absolute()
BASE_DIR = FILE_PATH.parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.configs.config import(
    ADVANCED_CHATAGENT_MODEL,
    BASE_DIR,
    OUTPUT_DIR,
    CHAT_AGENT_WORKERS,
    TASK_DIRS,
    MAINBODY_FILES,
    GENERATE_ONLY_RELATED_WORK,
    RELATED_WORK_SECTION_TITLE,
    RELATED_WORK_DESCRIPTION
)
from src.LLM.ChatAgent import ChatAgent
from src.LLM.utils import load_prompt
from src.modules.preprocessor.utils import parse_arguments_for_integration_test
from src.modules.utils import clean_chat_agent_format, load_file_as_string, save_result
from src.schema.base import Base
from src.schema.outlines import Outlines, SingleOutline

logger = logging.getLogger(__name__)

class ContentGenerator(Base):
    ITER_SPAN = 10
    
    def __init__(self, task_id:str):
        super().__init__(task_id)
        self.outlines_path = self.task_dir / "outlines.json"
        self.work_dir = self.task_dir
        self.papers_dir = self.papers_dir
        
    def process_response(self, response):
        res = clean_chat_agent_format(content=response)
        try:
            res_dic = json.loads(res)
            return res_dic
        except Exception as e:
            logger.warning(f"{str(e)}, Failed to process response {response[:100]}.")
            return None
        
    def mount_trees_on_outlines(self, trees_path, outlines, chat_agent):
        papers = []
        for file in os.listdir(trees_path):
            if not file.endswith(".json"):
                continue
            paper_path = trees_path / file
            paper_dic = json.loads(load_file_as_string(paper_path))
            if not "attri" in paper_dic:
                continue
            paper_dic["path"] = str(paper_path)
            papers.append(paper_dic)
            
        prompts_and_index = []
        for i, paper in enumerate(papers):
            prompt = load_prompt(
                f"{BASE_DIR}/resources/LLM/prompts/content_generator/mount_tree_on_outlines.md",
                outlines=str(outlines),
                paper=json.dumps(paper["attri"], indent=4),
            )
            prompts_and_index.append([prompt, i])
            
        retry = 0
        mount_l = [None] * len(papers)
        while prompts_and_index and retry < 3:
            prompts = [x[0] for x in prompts_and_index]
            response_l = chat_agent.batch_remote_chat(prompts, desc="mouting trees on outlines...")
            prompts_and_index_copy = []
            for response, (prompt, index) in zip(response_l, prompts_and_index):
                ans = self.process_response(response)
                if ans:
                    mount_l[index] = ans
                else:
                    prompts_and_index_copy.append([prompt, index])
            
            retry += 1
            prompts_and_index = prompts_and_index_copy
            
        for mount, paper in zip(mount_l, papers):
            paper["mount_outline"] = mount
            save_result(json.dumps(paper, indent=4), paper["path"])
            
            
    def draw_mount_details(self, paper_dir: Path, fig_path: Path) -> None:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        section_number_counter = Counter()
        for file in os.listdir(paper_dir):
            paper_path = paper_dir / file
            paper = json.loads(load_file_as_string(paper_path))
            if paper["mount_outline"] is not None:
                section_number_counter.update(
                    mount["section number"] for mount in paper["mount_outline"]
                )

        if not section_number_counter:
            print("Warning: No mount outline data found")
            return
            
        section_number_counter = sorted(section_number_counter.items())
        x = [snc[0] for snc in section_number_counter]
        y = [snc[1] for snc in section_number_counter]
        plt.figure(figsize=(10, 5))
        plt.bar(x, y)
        for i, value in enumerate(y):
            plt.text(i, value, str(value), ha="center", va="bottom")
        plt.title("mount details")
        plt.xticks(range(len(x)), [k for k in x])
        plt.xlabel("chapter")
        plt.ylabel("count")
        os.makedirs(fig_path.parent, exist_ok=True)
        plt.savefig(fig_path)
        plt.close()
        
    def map_section_to_papers(self, outlines, paper_dir):
        sec2info = {
            subsection.title : []
            for section in outlines.sections
            for subsection in [section] + section.sub
        }
        
        for file in os.listdir(paper_dir):
            if not file.endswith(".json"):
                continue
            p = paper_dir / file
            dic = json.loads(load_file_as_string(p))
            if not "mount_outline" in dic or dic["mount_outline"] is None:
                continue
            
            try:
                for mount in dic["mount_outline"]:
                    serial_no = mount["section number"]
                    key_info = mount["key information"]
                    single_outline = outlines.serial_no_to_single_outline(serial_no)
                    if single_outline:
                        sec2info[single_outline.title].append(
                            f"bib_name: {dic['bib_name']}\ninfo: {key_info}"
                        )
                        
            except Exception as e:
                tb_str = traceback.format_exc()
                logger.error(f"An error occurred: {e}; The traceback: {tb_str}")
        
        return sec2info
    
    def contains_markdown(self, text):
        markdown_patterns = [
            r"(^|\n)#{1,6} ", 
            r"(\*\*.*?\*\*|\*.*?\*)", 
            r"(^|\n)[\-\+\*] ", 
            r"(^|\n)\d+\."
        ]
        
        return any(re.search(pattern, text) for pattern in markdown_patterns)
    
    
    def write_content_iteratively(self, papers, outlines, written_content, last_written, subsection_title, subsection_desc, chat_agent):
        res = "**"
        prompt = load_prompt(
            f"{BASE_DIR}/resources/LLM/prompts/content_generator/fulfill_content_iteratively.md",
            topic=self.topic,
            outlines=str(outlines),
            content=written_content,
            papers="\n\n".join(papers),
            section_title=subsection_title,
            section_desc=subsection_desc,
            last_written=last_written,
        )
        while self.contains_markdown(res) == True:
            res = chat_agent.remote_chat(prompt, model=ADVANCED_CHATAGENT_MODEL)
            res = clean_chat_agent_format(res)
            
        res = res.replace("\\subsection{Conclusion}", "")
        return res
    
    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    def gen_single_section_words(self, section, chat_agent:ChatAgent):
        if '<section_words>' not in section:
            return section
        
        prompt = load_prompt(
            Path(BASE_DIR)
            / "resources"
            / "LLM"
            / "prompts"
            / "content_generator"
            / "write_section_words.md",
            section=section,
        )
        
        res = chat_agent.remote_chat(prompt)
        try:
            ans = re.findall(r"<answer>(.*?)</answer>", res, re.DOTALL)[0]
            ans = clean_chat_agent_format(ans)
        except:
            logger.error(
                f"Failed to get answer from the chat agent. The response is: {res}"
            )
            logger.error(f"Prompt: {prompt}")
            raise Exception("Failed to get answer from the chat agent")
        
        section = section.replace("<section_words>", ans)
        return section
    
    def gen_section_words(self, mainbody, chat_agent:ChatAgent):
        sections = re.split(r"(?=\\section\{)", mainbody.strip())
        sections = [
            section.strip() for section in sections if section.startswith("\\section{")
        ]
        add_insert_code = lambda section: re.sub(
            r"(\\section\{[^}]*\})",
            lambda x: f"{x.group(1)}\n<section_words>\n",
            section,
        )
        sections[2:-1] = [add_insert_code(section) for section in sections[2:-1]]
        logger.info("Start to generate section words.")
        pbar = tqdm(total=len(sections), desc="generating section words...")
        with ThreadPoolExecutor(max_workers=CHAT_AGENT_WORKERS) as executor:
            future_to_index = {
                executor.submit(self.gen_single_section_words, section, chat_agent): idx
                for idx, section in enumerate(sections)
            }
            for future in as_completed(future_to_index):
                result = future.result()
                idx = future_to_index[future]
                sections[idx] = result
                pbar.update(1)
            
        pbar.close()
        return "\n".join(sections)
    
    def content_fulfill_iter(self, paper_dir, outlines, chat_agent, mainbody_save_path):
        sec2info = self.map_section_to_papers(outlines, paper_dir)
        tqdm_bar = tqdm(
            total=sum(len(section.sub) + 1 for section in outlines.sections),
            desc="writing content...",
            position=0
        )
        written_content = (
            f"\\title{{{outlines.title}}}\n"
        )
        mainbody = []
        for i, section in enumerate(outlines.sections):
            out1_title = section.title
            written_content += f"\n\\section{{{out1_title}}}\n"
            mainbody.append(f"\\section{{{out1_title}}}")
            if section.sub == []:
                section.sub.append(SingleOutline(section.title, section.desc))
                
            for j, subsection in enumerate(section.sub):
                out2_title = subsection.title
                written_content += f"\n\\subsection{{{out2_title}}}\n"
                if not out2_title in sec2info:
                    papers = []
                else:
                    papers = sec2info[out2_title]
                    
                last_written = ""
                with tqdm(
                    total=max(len(papers), 1),
                    desc=f"{i + 1}.{j + 1} {subsection.title[:10]}",
                    position=1,
                    leave=False,
                ) as bar_2:
                    for k in range(0, max(len(papers), 1), self.ITER_SPAN):
                        tmp_papers = papers[k : k + self.ITER_SPAN]
                        res = self.write_content_iteratively(
                            papers=tmp_papers,
                            outlines=outlines,
                            written_content=written_content,
                            last_written=last_written,
                            subsection_title=subsection.title,
                            subsection_desc=subsection.desc,
                            chat_agent=chat_agent
                        )
                        last_written = res
                        bar_2.update(10)
                        
            mainbody.append(last_written)
            written_content = "\n\n".join(mainbody)
            tqdm_bar.update()
            
        tqdm_bar.close()
        mainbody = "\n\n".join(mainbody)
        mainbody = self.gen_section_words(mainbody, chat_agent)
        save_result(mainbody, mainbody_save_path)
        logger.info("content fulfill done.")
        
    def content_fulfill(self, paper_dir, outlines, chat_agent, mainbody_save_path):
        sec2info = self.map_section_to_papers(outlines, paper_dir)
        tqdm_bar = tqdm(
            total=sum(
                1 if len(section.sub) == 0 else len(section.sub)
                for section in outlines.sections
            ),
            desc="writing content...",
        )
        written_content = f"\\title{{{outlines.title}}}\n"
        mainbody = []
        for i, section in enumerate(outlines.sections):
            out1_title = section.title
            written_content += f"\n\\section{{{out1_title}}}\n"
            mainbody.append(f"\n\\section{{{out1_title}}}\n")
            if section.sub == []:
                section.sub.append(SingleOutline(section.title, section.desc))
            for j, subsection in enumerate(section.sub):
                out2_title = subsection.title
                written_content += f"\n\\subsection{{{out2_title}}}\n"
                if not out2_title in sec2info:
                    papers = []
                else:
                    papers = sec2info[out2_title]
                
                res = "**"
                prompt = load_prompt(
                    f"{BASE_DIR}/resources/LLM/prompts/content_generator/fulfill_content.md",
                    topic=self.topic,
                    outlines=str(outlines),
                    content=written_content,
                    papers="\n\n".join(papers),
                    section_title=subsection.title,
                    section_desc=subsection.desc,
                )
                while self.contains_markdown(res) == True:
                    res = chat_agent.remote_chat(prompt, model = ADVANCED_CHATAGENT_MODEL)
                    res = clean_chat_agent_format(res)
                    
                res = res.replace("\\subsection{Conclusion}", "")
                mainbody.append(res)
                written_content = "\n\n".join(mainbody)
                tqdm_bar.update()
                
        tqdm_bar.close()
        save_result("\n\n".join(mainbody), mainbody_save_path)
        logger.info("Content fulfill done.")
        
    def gen_abstract(self, mainbody_raw_path, abstract_save_path, chat_agent:ChatAgent):
        mainbody_raw = open(mainbody_raw_path, "r", encoding="utf-8").read()
        prompt = load_prompt(
            f"{BASE_DIR}/resources/LLM/prompts/content_generator/write_abstract.md",
            topic=self.topic,
            mainbody_raw=mainbody_raw,
        )
        logger.debug("Generating abstract.")
        abstract = chat_agent.remote_chat(prompt, model=ADVANCED_CHATAGENT_MODEL)
        abstract = abstract.split("<abstract>")[-1].split("</abstract>")[0].strip()
        abstract = "\n\\begin{abstract}\n" + abstract + "\n\\end{abstract}\n"
        save_result(abstract, abstract_save_path)
        
    def post_revise(self, mainbody_raw_path, mainbody_save_path, papers_dir):
        extract_braced_content = lambda s: (
            m.group(1) if (m := re.search(r"\{(.*?)\}", s)) else None
        )
        mainbody_raw = load_file_as_string(mainbody_raw_path)
        filter = set(["in conclusion", "in summary", "in essence"])
        legal_cite = [
            json.loads(load_file_as_string(papers_dir / f))["bib_name"]
            for f in os.listdir(papers_dir)
        ]
        
        mainbody = []
        for line in mainbody_raw.splitlines(keepends=True):
            if any(e in line.lower() for e in filter):
                continue
            citations = re.findall(r"\\cite\{(.*?)\}", line)
            for citation in citations:
                if citation not in legal_cite:
                    line = line.replace(f"\\cite{{{citation}}}", "")
                    
            if r"\section" in line:
                section_name = extract_braced_content(line)
                line = line.strip() + f" \\label{{sec:{section_name}}}\n"
            elif r"\subsection" in line:
                section_name = extract_braced_content(line)
                line = line.strip() + f" \\label{{subsec:{section_name}}}\n"
                
            line = re.sub(
                r"\\textit\{([^}]*)\}",
                lambda m: "\\textit{" + m.group(1).replace("_", "") + "}",
                line,
            )
            mainbody.append(line)
            
        save_result("\n".join(mainbody), mainbody_save_path)
        
    def generate_related_work_only(self):
        chat_agent = ChatAgent()
        outlines = Outlines.from_saved(self.outlines_path)
        
        self.mount_trees_on_outlines(self.papers_dir, outlines, chat_agent)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        
        sec2info = self.map_section_to_papers(outlines, self.papers_dir)
        
        all_papers = []
        for info_list in sec2info.values():
            all_papers.extend(info_list)
        
        pdf_papers = []
        for file in os.listdir(self.papers_dir):
            if not file.endswith(".json"):
                continue
            p = self.papers_dir / file
            paper_data = json.loads(load_file_as_string(p))
            if paper_data.get("from") == "pdf":
                pdf_info = f"bib_name: {paper_data.get('bib_name', '')}\ninfo: 用户上传的重要参考文献 - {paper_data.get('title', '')}"
                pdf_papers.append(pdf_info)

        pdf_paper_bibs = [info.split("bib_name: ")[1].split("\n")[0] for info in pdf_papers]
        existing_bibs = [info.split("bib_name: ")[1].split("\n")[0] for info in all_papers if "bib_name: " in info]
        for pdf_info in pdf_papers:
            pdf_bib = pdf_info.split("bib_name: ")[1].split("\n")[0]
            if pdf_bib not in existing_bibs:
                all_papers.append(pdf_info)

        if len(all_papers) > 100:
            pdf_infos = [p for p in all_papers if any(f"bib_name: {pdf_bib}" in p for pdf_bib in pdf_paper_bibs)]
            other_infos = [p for p in all_papers if not any(f"bib_name: {pdf_bib}" in p for pdf_bib in pdf_paper_bibs)]
            all_papers = pdf_infos + other_infos[:100]
            logger.info(f"Paper information too large, truncated to {len(all_papers)} items while preserving PDF papers")
    
            
        mainbody = [f"\\section{{{RELATED_WORK_SECTION_TITLE}}}"]
        
        prompt = load_prompt(
            f"{BASE_DIR}/resources/LLM/prompts/content_generator/fulfill_content.md",
            topic=self.topic,
            outlines=str(outlines),
            content="",
            papers="\n\n".join(all_papers),
            section_title=RELATED_WORK_SECTION_TITLE,
            section_desc=RELATED_WORK_DESCRIPTION,
        )
        
        res = chat_agent.remote_chat(prompt, model=ADVANCED_CHATAGENT_MODEL)
        res = clean_chat_agent_format(res)
        mainbody.append(res)
        
        mainbody_content = "\n\n".join(mainbody)
        mainbody_save_path = self.tmp_dir / MAINBODY_FILES["RELATED_WORK"]
        save_result(mainbody_content, mainbody_save_path)
        
        mainbody_final_path = self.tmp_dir / MAINBODY_FILES["INITIAL"]
        self.post_revise(mainbody_save_path, mainbody_final_path, self.papers_dir)
        logger.info("Related Work generation done.")
        
        return mainbody_final_path
        
    def run(self):
        chat_agent = ChatAgent()
        outlines = Outlines.from_saved(self.outlines_path)
        if GENERATE_ONLY_RELATED_WORK:
            return self.generate_related_work_only()
        
        self.mount_trees_on_outlines(self.papers_dir, outlines, chat_agent)
        
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        
        mount_detail_fig_path = self.tmp_dir / "mount_details.jpg"
        self.draw_mount_details(self.papers_dir, mount_detail_fig_path)
        
        mainbody_raw_path = self.tmp_dir / MAINBODY_FILES["INITIAL"]
        self.content_fulfill_iter(self.papers_dir, outlines, chat_agent, mainbody_raw_path)
        
        abstract_raw_path = self.tmp_dir / "abstract.tex"
        self.gen_abstract(mainbody_raw_path, abstract_raw_path, chat_agent)

        mainbody_save_path = self.tmp_dir / MAINBODY_FILES["FINAL"]
        self.post_revise(mainbody_raw_path, mainbody_save_path, self.papers_dir)