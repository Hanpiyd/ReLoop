from pathlib import Path
import traceback
import logging
import re

from src.configs.config import OUTPUT_DIR, RESOURCE_DIR, TASK_DIRS, MAINBODY_FILES
from src.configs.utils import load_latest_task_id
from src.LLM.ChatAgent import ChatAgent
from src.modules.latex_handler.latex_comparison_table_builder import (
    LatexComparisonTableBuilder,
)
from src.modules.latex_handler.latex_figure_builder import LatexFigureBuilder
from src.modules.latex_handler.latex_list_table_builder import LatexListTableBuilder
from src.modules.latex_handler.latex_summary_table_builder import (
    LatexSummaryTableBuilder,
)
from src.modules.latex_handler.latex_figure_builder import LatexFigureBuilder
from src.modules.latex_handler.latex_summary_table_builder import (
    LatexSummaryTableBuilder,
)
from src.modules.post_refine.rag_refiner import RagRefiner
from src.modules.post_refine.rule_based_refiner import RuleBasedRefiner
from src.modules.post_refine.section_rewriter import SectionRewriter
from src.modules.post_refine.base_refiner import BaseRefiner
from src.modules.utils import save_result, load_file_as_string
from src.configs.utils import ensure_task_dirs

logger = logging.getLogger(__name__)

class PostRefiner(BaseRefiner):
    def __init__(self, task_id:str = None, **kwargs):
        llamaindex_topk = (
            30 if "llamaindex_topk" not in kwargs else kwargs["llamaindex_topk"]
        )
        task_id = load_latest_task_id() if task_id is None else task_id
        super().__init__(task_id, **kwargs)
        
        self.task_dir = ensure_task_dirs(task_id)
        self.paper_dir = self.task_dir / TASK_DIRS["PAPERS_DIR"]
        self.tmp_dir = self.task_dir / TASK_DIRS["TMP_DIR"]
        
        self.mainbody_path = self.tmp_dir / MAINBODY_FILES["INITIAL"]
        self.refined_mainbody_path = self.tmp_dir / MAINBODY_FILES["FINAL"]
        
        self.max_retry_times = 2
        self.max_words = 10000
        if "papers" not in kwargs:
            self.papers = self.load_papers(self.paper_dir)
        else:
            self.papers = kwargs["papers"]
        logger.info(f"PostRefiner load {len(self.papers)} papers")
        self.llamaindex_topk = llamaindex_topk
        self.llamaindex_store_local = False
        if "llamaindex_wrapper" in kwargs:
            self.llamaindex_wrapper = kwargs["llamaindex_wrapper"]
            self.rag_refiner = RagRefiner(
                task_id,
                llamaindex_store_local=self.llamaindex_store_local,
                papers=self.papers,
                llamaindex_wrapper=self.llamaindex_wrapper,
            )
        else:
            self.rag_refiner = RagRefiner(
                task_id,
                llamaindex_store_local=self.llamaindex_store_local,
                papers=self.papers,
            )
            self.llamaindex_wrapper = self.rag_refiner.llamaindex_wrapper
        self.sec_rewriter = SectionRewriter(task_id, papers=self.papers)
        self.rule_based_refiner = RuleBasedRefiner(task_id=task_id, papers=self.papers)
        chat_agent = ChatAgent()
        self.tmp_path = self.tmp_dir / "table_gen"
        self.latex_path = self.task_dir / TASK_DIRS["LATEX_DIR"]
        self.prompt_dir = Path(f"{RESOURCE_DIR}/LLM/prompts/latex_table_builder")
        self.mainbody_tex_path = self.tmp_dir / MAINBODY_FILES["FINAL"]
        self.outlines_path = self.task_dir / "outlines.json"

        self.fig_builder = LatexFigureBuilder(task_id=task_id)
        self.summary_table_builder = LatexSummaryTableBuilder(
            main_body_path=self.mainbody_tex_path,
            tmp_path=self.tmp_path,
            outline_path=self.outlines_path,
            latex_path=self.latex_path,
            paper_dir=self.paper_dir,
            prompt_dir=self.prompt_dir,
            chat_agent=chat_agent,
        )
        self.list_table_builder = LatexListTableBuilder(
            main_body_path=self.mainbody_tex_path,
            tmp_path=self.tmp_path,
            outline_path=self.outlines_path,
            latex_path=self.latex_path,
            paper_dir=self.paper_dir,
            prompt_dir=self.prompt_dir,
            chat_agent=chat_agent,
        )
        self.comparison_table_builder = LatexComparisonTableBuilder(
            main_body_path=self.mainbody_tex_path,
            tmp_path=self.tmp_path,
            outline_path=self.outlines_path,
            latex_path=self.latex_path,
            paper_dir=self.paper_dir,
            prompt_dir=self.prompt_dir,
            chat_agent=chat_agent,
        )

    def generate_tables(self):
        try:
            self.summary_table_builder.run()
        except Exception as e:
            tb_str = traceback.format_exc()
            logger.error(f"An error occurred: {e}; The traceback: {tb_str}")

        try:
            self.list_table_builder.run()
        except Exception as e:
            tb_str = traceback.format_exc()
            logger.error(f"An error occurred: {e}; The traceback: {tb_str}")

        try:
            self.comparison_table_builder.run()
        except Exception as e:
            tb_str = traceback.format_exc()
            logger.error(f"An error occurred: {e}; The traceback: {tb_str}")

    def extract_used_citations(self, mainbody_path=None, references_bib_path=None, output_bib_path=None):
        if mainbody_path is None:
            mainbody_path = self.refined_mainbody_path
        if references_bib_path is None:
            references_bib_path = self.latex_dir / "references.bib"
        if output_bib_path is None:
            output_bib_path = self.latex_dir / "used_references.bib"

        mainbody_content = load_file_as_string(mainbody_path)
        cite_pattern = r'\\cite[tp]?\{([^}]+)\}'
        all_citations = re.findall(cite_pattern, mainbody_content)
        used_citations = set()
        for citation_group in all_citations:
            for citation in citation_group.split(','):
                used_citations.add(citation.strip())
        logger.info(f"在mainbody中找到{len(used_citations)}个唯一引用")

        bib_content = load_file_as_string(references_bib_path)
        bib_pattern = r'(@\w+\{[^,]+,.*?(?=@|\Z))'
        all_bib_entries = re.findall(bib_pattern, bib_content, re.DOTALL)

        bib_dict = {}
        for entry in all_bib_entries:
            key_match = re.search(r'@\w+\{([^,]+),', entry)
            if key_match:
                key = key_match.group(1).strip()
                bib_dict[key] = entry
        logger.info(f"在references.bib中找到{len(bib_dict)}个条目")

        used_bib_entries = []
        for citation in used_citations:
            if citation in bib_dict:
                used_bib_entries.append(bib_dict[citation])
            else:
                logger.warning(f"引用'{citation}'在references.bib中未找到")

        with open(output_bib_path, "w", encoding="utf-8") as f:
            f.write('\n\n'.join(used_bib_entries))
        
        logger.info(f"已将{len(used_bib_entries)}个使用过的引用保存到{output_bib_path}")
    
        return output_bib_path

    def run(self, mainbody_path = None, GENERATE_RELATED_WORK_ONLY:bool = False, GENERATE_PROPOSAL:bool = False):
        if mainbody_path is None:
            mainbody_path = self.mainbody_path
        
        try_times = 0
        while try_times < self.max_retry_times:
            self.rag_refiner.run(mainbody_path)
            self.sec_rewriter.run(self.rag_refiner.refined_mainbody_path, GENERATE_RELATED_WORK_ONLY, GENERATE_PROPOSAL)
            if not (GENERATE_RELATED_WORK_ONLY or GENERATE_PROPOSAL):
                self.fig_builder.run(mainbody_path=self.sec_rewriter.refined_mainbody_path)
                final_refined_content = self.rule_based_refiner.run(self.fig_builder.fig_mainbody_path)
            else:
                final_refined_content = self.rule_based_refiner.run(self.sec_rewriter.refined_mainbody_path)
            save_result(final_refined_content, self.refined_mainbody_path)
            if not (GENERATE_RELATED_WORK_ONLY or GENERATE_PROPOSAL):
                self.generate_tables()
            words_count = len(final_refined_content.strip().split())
            if words_count < self.max_words:
                logger.debug(f"核验通过，postrefine后的main body总字数为{words_count}")
                break
            else:
                try_times += 1
                logger.debug(
                    f"核验不通过，postrefine后的main body总字数为{words_count}；postrefine后的main重新生成，trying times {try_times}, max trying: {self.max_retry_times}"
                )

        self.extract_used_citations()
        logger.info(f"Post refine and save content to {self.refined_mainbody_path}")
        print("\n" + "="*80)
        print("内容优化完成！如果您对结果不满意，可以使用reloop.py进行回环处理。")
        print("使用方法：")
        print(f"python -m src.reloop --task_id {self.task_id} [选项]")
        print("\n可用选项:")
        print("  --keywords_add \"keyword1,keyword2\"    添加新的关键词")
        print("  --keywords_remove \"keyword1,keyword2\" 移除不相关的关键词")
        print("  --papers_keep \"paper_id1,paper_id2\"   指定要保留的论文ID")
        print("  --papers_exclude \"paper_id1,paper_id2\" 指定要排除的论文ID")
        print("  --topics_emphasize \"topic1,topic2\"    强调特定主题")
        print("  --topics_reduce \"topic1,topic2\"       减少特定主题的关注度")
        print("  --feedback_text \"您的反馈\"             添加一般性反馈")
        print("  --skip_recall                         跳过重新检索论文阶段")
        print("="*80 + "\n")
        return final_refined_content