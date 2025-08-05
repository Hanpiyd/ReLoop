import json
import os
import random as normalrandom
import re
import traceback
from pathlib import Path
import logging
from tqdm import tqdm
from src.configs.config import(
    OUTPUT_DIR,
    RESOURCE_DIR,
    TASK_DIRS,
    MAINBODY_FILES
)
from src.configs.utils import load_latest_task_id, ensure_task_dirs
from src.models.rag.modeling_llamaidx import Document, LlamaIndexWrapper
from src.modules.post_refine.base_refiner import BaseRefiner
from src.modules.utils import load_file_as_string, load_prompt, save_result
from src.schema.paragraph import Paragraph

logger = logging.getLogger(__name__)

class RagRefiner(BaseRefiner):
    def __init__(self, task_id: str = None, **kwargs) -> None:
        super().__init__(task_id, **kwargs)
        
        self.task_dir = ensure_task_dirs(self.task_id)
        self.tmp_dir = self.task_dir / TASK_DIRS["TMP_DIR"]
        
        self.refined_mainbody_path = self.tmp_dir / MAINBODY_FILES["RAG"]
        
        self.llamaindex_embed_model = None
        self.llamaindx_index = None
        self.llamaindex_wrapper = None
        self.llamaindex_retriever = None
        self.llamaindex_topk = (
            5 if "llamaindex_topk" not in kwargs else kwargs["llamaindex_topk"]
        )
        self.llamaindex_store_local = (
            kwargs["llamaindex_store_local"]
            if "llamaindex_store_local" in kwargs
            else False
        )
        self.llamaindex_score_threshold = (
            0.2 
        )
        self.paragraph_citation_random_start = 2
        self.paragraph_citation_random_end = self.llamaindex_topk
        self.paragraph_sentence_sampling_rate = 0.3
        self.refine_prompt_dir = Path(f"{RESOURCE_DIR}/LLM/prompts/rag_refiner")
        self.skip_words = ["\\cite", "\\autoref", "\\figure", "\\table"]
        if "llamaindex_wrapper" in kwargs:
            self.llamaindex_wrapper = kwargs["llamaindex_wrapper"]
            self.llamaindx_index = self.llamaindex_wrapper.index
            self.llamaindex_retriever = self.llamaindex_wrapper.get_retriever(
                self.llamaindx_index, top_k=self.llamaindex_topk
            )
        else:
            llamaindex_wrapper = None
            self.init_llamaindex(
                papers=self.papers, llamaindex_wrapper=llamaindex_wrapper
            )
        
    def clean_paper_content(self, content):
        pattern = r"#\s*(.*?)\s*(?=#|$)"
        matches = re.findall(pattern, content, re.DOTALL | re.IGNORECASE)
        try:
            seg_collection = []
            for one_idx, one in enumerate(matches[1:]):
                if one_idx > 8 or "method" in one.lower()[:10]:
                    break
                temp = f"# {one}"
                seg_collection.append(temp)
            cleaned_content = "\n\n".join(seg_collection)
        except Exception as e:
            logger.error(f"clean paper content has an error: {e}")
            cleaned_content = content
        return cleaned_content
    
    def init_llamaindex(self, papers = None, llamaindex_wrapper = None):
        if llamaindex_wrapper is not None:
            self.llamaindex_wrapper = llamaindex_wrapper
            self.llamaindex_retriever = self.llamaindex_wrapper.get_retriever(
                self.llamaindex_wrapper.index, top_k=self.llamaindex_topk
            )
        else:
            agent = LlamaIndexWrapper(
                embed_model=self.llamaindex_embed_model, llm_model=None
            )
            self.llamaindex_wrapper = agent
            
        docs_for_llamaindex = []
        for one in papers:
            try:
                title = one["title"].strip()
                abstract = one["abstract"].strip()
                bib_name = one["bib_name"].strip()
            except Exception as e:
                logger.debug(f"{e}")
                continue
            llamaindex_doc = Document(
                text=title + " " + abstract,
                metadata={
                    "title": title,
                    "bib_name": bib_name,
                },
            )
            docs_for_llamaindex.append(llamaindex_doc)
        md_nodes = docs_for_llamaindex
        logger.debug(
            f"===== create_vector_index for {len(md_nodes)} text nodes from {len(docs_for_llamaindex)} papers. ======"
        )
        index = self.llamaindex_wrapper.create_vector_index(
            nodes=md_nodes, store_local=self.llamaindex_store_local
        )
        self.llamaindex_retriever = self.llamaindex_wrapper.get_retriever(
            index=index, top_k=self.llamaindex_topk
        )
        
    def rewrite_sent_with_citations(self, sent, citation_contents, bib_list):
        citations = " - " + "\n - ".join(citation_contents) + "\n"
        prompt = load_prompt(
            filename=str(
                self.refine_prompt_dir.joinpath("rag_rewrite_sentence.md").absolute()
            ),
            sent=sent,
            citations=citations,
        )
        result = self.chat_agent.remote_chat(prompt)
        bib_content = " \\cite{" + ",".join(bib_list) + "}"
        return result + bib_content
    
    def filter_results_by_scores(self, nodes, threshold):
        filtered = []
        for one in nodes:
            if one.score >= threshold:
                filtered.append(one)
        return filtered
    
    def refine_a_paragraph(self, paragraph: str, num_citations: int = None):
        if num_citations is None:
            num_citations = normalrandom.randint(
                self.paragraph_citation_random_start, self.paragraph_citation_random_end
            )
            
        sent_list = paragraph.strip().split(". ")
        sent_list_length = len(sent_list)
        num_sent = int(sent_list_length * self.paragraph_sentence_sampling_rate)
        num_sent = min(max(1, num_sent), 3)
        sampled_indices = normalrandom.sample(range(sent_list_length), num_sent)
        success_count = 0
        for sent_id in range(sent_list_length):
            if sent_id not in sampled_indices:
                continue
            sent = sent_list[sent_id]
            if "\\cite" in sent:
                continue
            query = load_prompt(
                filename=str(
                    self.refine_prompt_dir.joinpath(
                        "retrieve_paper_segments.md"
                    ).absolute()
                ),
                query=sent,
            )
            results = self.llamaindex_retriever.retrieve(query)[:num_citations]
            results = self.filter_results_by_scores(
                nodes=results, threshold=self.llamaindex_score_threshold
            )
            if len(results) < 1:
                continue
            citation_content_list = [one.text for one in results]
            bib_list = [one.metadata["bib_name"] for one in results]
            bib_list = list(set(bib_list))
            new_sent = self.rewrite_sent_with_citations(
                sent=sent, citation_contents=citation_content_list, bib_list=bib_list
            )
            paragraph = paragraph.replace(sent, new_sent)
            success_count += 1
        return paragraph, success_count
    
    def refine_a_section(self, section: Paragraph, sec_id: int):
        revised_content = section.content
        para_list = section.content.strip().split("\n")
        para_list = [
            one for one in para_list if ("section" not in one and one.strip() != "")
        ]
        para_list_length = len(para_list)
        success_count_total = 0
        for para_id in tqdm(
            range(para_list_length), desc=f"refining paragraphs in section {sec_id} ..."
        ):
            para = para_list[para_id]
            skip_flag = False
            for one in self.skip_words:
                if one in para:
                    skip_flag = True
            if skip_flag:
                continue
            logger.debug(f"refining paragraph id {para_id} in section {sec_id}")
            new_para, success_count = self.refine_a_paragraph(
                paragraph=para, num_citations=None
            )
            revised_content = revised_content.replace(para, new_para)
            success_count_total += success_count
            
        new_section = Paragraph.from_section(section=revised_content, no=section.no)
        return new_section, success_count_total
    
    def run(self, mainbody_path = None):
        if mainbody_path is None:
            mainbody_path = self.tmp_dir / MAINBODY_FILES["INITIAL"]
            
        survey_sections = self.load_survey_sections(mainbody_path)
        refined_survey = []
        for section in survey_sections[:-1]:
            refined_section, success_count = self.refine_a_section(
                section=section, sec_id=section.no
            )
            refined_survey.append(refined_section.content)
            logger.info(
                f"Successfully refine {success_count} paragraphs in section {section.no}"
            )
        refined_survey.append(survey_sections[-1].content)
        refined_content = "\n".join(refined_survey)
        save_result(refined_content, self.refined_mainbody_path)
        logger.debug(f"Save content to {self.refined_mainbody_path}.")
        return refined_content