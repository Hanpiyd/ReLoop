from collections import Counter
import json
import os
import re
import time
import random
from pathlib import Path
from urllib.parse import quote_plus, urlencode
from typing import List, Dict, Any, Optional
from tqdm import tqdm
import logging
import pickle
import multiprocessing as mp


try:
    from serpapi import GoogleSearch
    SERPAPI_AVAILABLE = True
except ImportError:
    SERPAPI_AVAILABLE = False
    GoogleSearch = None

try:
    import orjson
    HAS_ORJSON = True
except ImportError:
    HAS_ORJSON = False
    
try:
    import ujson
    HAS_UJSON = True
except ImportError:
    HAS_UJSON = False

from src.configs.config import (
    AVAILABLE_DATA_SOURCES,
    CACHE_DIR,
    OUTPUT_DIR,
    DATASET_DIR,
    PAPERS_DIR,
    DEFAULT_DATA_FETCHER_ENABLE_CACHE,
    SERPAPI_API_KEY
)

from src.modules.utils import load_file_as_string, save_result, sanitize_filename

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

class DataFetcher:
    SINGLE_WORD_LIMIT = 1000
    
    def __init__(self, papers_dir:str = PAPERS_DIR, enable_cache:bool = DEFAULT_DATA_FETCHER_ENABLE_CACHE):
        logger.info("begin init fetcher")
        self.papers_dir = Path(papers_dir)
        self.enable_cache = enable_cache
        self.papers_dir.mkdir(parents=True, exist_ok=True)
        self.dataset_dir = Path(f"{DATASET_DIR}/raw")
        self.paper_store_dir = self.dataset_dir / "papers"
        self.mapping_file_path = self.dataset_dir / "mappings.json"
        self.paper_store_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file_path = Path(CACHE_DIR) / "key_words_cache.json"
        self.mapping_dict = self._load_mapping_dict()
        self.cache_dict = self._load_cache_dict()
        logger.info("1")
        self.papers_metadata = self._load_all_papers_metadata()
        logger.info("2")
        self._check_serpapi_setup()
        
    def _check_serpapi_setup(self):
        if not SERPAPI_AVAILABLE:
            logger.error("SerpAPI库未安装，请运行: pip install google-search-results")
            logger.error("Google Scholar搜索功能将不可用")
            return False
            
        if not SERPAPI_API_KEY:
            logger.warning("未找到SERPAPI_API_KEY，Google Scholar搜索功能将不可用")
            return False
            
        logger.info("SerpAPI配置正常")
        return True
        
    def _load_mapping_dict(self):
        if self.mapping_file_path.exists():
            file_content = self.mapping_file_path.open("r", encoding="utf-8").read()
            return json.loads(file_content)
        else:
            return dict([
                (data_src, {"title_to_id": {}, "id_to_title": {}})
                for data_src in AVAILABLE_DATA_SOURCES
            ])
            
    def _load_cache_dict(self):
        if self.cache_file_path.exists():
            file_content = self.cache_file_path.open("r", encoding="utf-8").read()
            return json.loads(file_content)
        else:
            return dict([
                (data_src, {"kw_to_ids": {}})
                for data_src in AVAILABLE_DATA_SOURCES
            ])
    
    def _read_json(self, file_path):
        if HAS_ORJSON:
            with open(file_path, "rb") as f:
                return orjson.loads(f.read())
        elif HAS_UJSON:
            with open(file_path, "r", encoding="utf-8") as f:
                return ujson.load(f)
        else:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
    
    def _process_batch(self, file_paths):
        batch_results = {}
        for file_path in file_paths:
            try:
                paper = self._read_json(file_path)
                
                if "_id" not in paper:
                    paper["_id"] = file_path.stem
                if "title" not in paper:
                    paper["title"] = file_path.stem
                if "from" not in paper:
                    if "arxiv" in str(file_path).lower() or "arxiv" in str(paper.get("detail_url", "")):
                        paper["from"] = "arxiv"
                    else:
                        paper["from"] = "local"
                
                batch_results[paper["_id"]] = {
                    "title": paper["title"],
                    "abstract": paper.get("abstract", ""),
                    "from": paper["from"],
                    "file_path": str(file_path)
                }
            except Exception as e:
                batch_results[f"error_{file_path}"] = str(e)
        
        return batch_results
            
    def _load_all_papers_metadata(self):
        cache_file = self.papers_dir / "papers_metadata_cache.pkl"
        if cache_file.exists():
            cache_mtime = cache_file.stat().st_mtime
            try:
                newest_json = max(self.papers_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, default=None)
                if newest_json and cache_mtime > newest_json.stat().st_mtime:
                    start_time = time.time()
                    with open(cache_file, "rb") as f:
                        papers_metadata = pickle.load(f)
                    elapsed = time.time() - start_time
                    logger.info(f"从缓存加载了 {len(papers_metadata)} 篇论文元数据，耗时 {elapsed:.2f} 秒")
                    return papers_metadata
            except Exception as e:
                logger.warning(f"检查缓存时出错: {str(e)}，将重新生成缓存")
        
        start_time = time.time()
        
        all_files = list(self.papers_dir.glob("*.json"))
        total_files = len(all_files)
        logger.info(f"找到 {total_files} 个JSON文件需要处理")
        cpu_count = mp.cpu_count()
        num_processes = max(1, min(cpu_count - 1, 16))
        batch_size = max(100, min(1000, total_files // (num_processes * 10)))
        batches = [all_files[i:i + batch_size] for i in range(0, len(all_files), batch_size)]

        papers_metadata = {}
        error_count = 0
        processed_count = 0

        with mp.Pool(num_processes) as pool:
            for i, batch_result in enumerate(pool.imap_unordered(self._process_batch, batches)):
                batch_errors = {k: v for k, v in batch_result.items() if k.startswith("error_")}
                for error_key in batch_errors:
                    file_path = error_key.replace("error_", "")
                    logger.error(f"加载论文 {file_path} 时出错: {batch_result[error_key]}")
                    del batch_result[error_key]
                    error_count += 1
                papers_metadata.update(batch_result)
                processed_count += len(batch_result)

                if (i + 1) % max(1, len(batches) // 20) == 0 or (i + 1) == len(batches):
                    progress = (i + 1) / len(batches) * 100
                    elapsed = time.time() - start_time
                    papers_per_second = processed_count / elapsed if elapsed > 0 else 0
                    estimated_total = (elapsed / progress) * 100 if progress > 0 else 0
                    remaining = max(0, estimated_total - elapsed)
                    
                    logger.info(f"进度: {i+1}/{len(batches)} 批次 ({progress:.1f}%) | "
                            f"已处理: {processed_count} 篇 | 错误: {error_count} 篇 | "
                            f"速度: {papers_per_second:.1f} 篇/秒 | "
                            f"已用时间: {elapsed:.1f}秒 | "
                            f"预计剩余: {remaining:.1f}秒")
                    
        try:
            with open(cache_file, "wb") as f:
                pickle.dump(papers_metadata, f, protocol=pickle.HIGHEST_PROTOCOL)
            logger.info(f"元数据缓存已保存至 {cache_file}")
        except Exception as e:
            logger.error(f"保存缓存时出错: {str(e)}")

        total_time = time.time() - start_time
        logger.info(f"从目录 {self.papers_dir} 加载了 {len(papers_metadata)} 篇论文元数据，耗时 {total_time:.2f} 秒")

        return papers_metadata
    
    def _load_paper(self, paper_id):
        if paper_id not in self.papers_metadata:
            return
        file_path = self.papers_metadata[paper_id]["file_path"]
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载论文 {file_path} 时出错: {str(e)}")
            return None
    
    def _search_google_scholar_with_serpapi(self, params):
        if not SERPAPI_AVAILABLE:
            logger.error("SerpAPI库未安装")
            return None
            
        if not SERPAPI_API_KEY:
            logger.error("SERPAPI_API_KEY未配置")
            return None
            
        try:
            search_params = {
                "engine": "google_scholar",
                "api_key": SERPAPI_API_KEY,
                "num" : 1,
                **params
            }
            
            logger.debug(f"正在调用SerpAPI: {search_params}")
            search = GoogleSearch(search_params)
            results = search.get_dict()
            
            if "error" in results:
                logger.error(f"SerpAPI错误: {results['error']}")
                return None
                
            return results
            
        except Exception as e:
            logger.error(f"SerpAPI搜索失败: {str(e)}")
            return None
    
    def _parse_serpapi_scholar_result(self, result):
        try:
            paper_id = f"gs_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
            
            title = result.get("title", "").strip()
            link = result.get("link", "")
            snippet = result.get("snippet", "")
            result_id = result.get("result_id", "")
            
            publication_info = result.get("publication_info", {})
            authors_str = ""
            year = ""
            venue = ""
            
            if isinstance(publication_info, dict) and "summary" in publication_info:
                summary = publication_info.get("summary", "")
                parts = summary.split(" - ")
                if len(parts) >= 1:
                    authors_str = parts[0].strip() 
                for part in parts:
                    year_match = re.search(r'\b(19|20)\d{2}\b', part)
                    if year_match:
                        year = year_match.group(0)
                
                if len(parts) > 1:
                    venue = parts[-1].strip()
            
            citations = 0
            inline_links = result.get("inline_links", {})
            if "cited_by" in inline_links:
                cited_by = inline_links["cited_by"]
                if isinstance(cited_by, dict) and "total" in cited_by:
                    citations = cited_by["total"]
            
            pdf_link = ""
            resources = result.get("resources", [])
            for resource in resources:
                if isinstance(resource, dict) and resource.get("title", "").lower() in ["pdf", "[pdf]"]:
                    pdf_link = resource.get("link", "")
                    break
            
            paper = {
                "_id": paper_id,
                "title": title,
                "abstract": snippet,
                "authors": authors_str,
                "publication": venue,
                "year": year,
                "citations": citations,
                "link": link,
                "pdf_link": pdf_link,
                "result_id": result_id,
                "from": "google_scholar",
                "fetched_at": int(time.time()),
                "serpapi_data": result
            }
            
            self._generate_bibtex_citation(paper)
                
            return paper
            
        except Exception as e:
            logger.error(f"解析SerpAPI结果失败: {str(e)}")
            logger.debug(f"原始结果: {result}")
            return None
    
    def _generate_bibtex_citation(self, paper):
        try:
            title = paper.get("title", "")
            authors_str = paper.get("authors", "")
            year = paper.get("year", "")
            venue = paper.get("publication", "")
            link = paper.get("link", "")
            if authors_str and year and title:
                first_author = authors_str.split(',')[0].split(' ')[-1] if ',' in authors_str else authors_str.split(' ')[0]
                first_word = title.split()[0] if title.split() else "paper"
                bib_name = f"{first_author}{year}{first_word}".lower()
            else:
                bib_name = f"paper{year}".lower()
            
            bib_name = re.sub(r'[^\w]', '', bib_name)
            
            reference = f"@article{{{bib_name},\n"
            if title:
                reference += f"  title={{{title}}},\n"
            if authors_str:
                reference += f"  author={{{authors_str}}},\n"
            if venue:
                reference += f"  journal={{{venue}}},\n"
            if year:
                reference += f"  year={{{year}}},\n"
            if link:
                reference += f"  url={{{link}}},\n"
            reference += "}"
            
            paper["reference"] = reference
            paper["bib_name"] = bib_name
            
        except Exception as e:
            logger.warning(f"创建引用格式失败: {str(e)}")
    
    def search_on_google(self, key_words: str, page: str, time_s: str = "", time_e: str = ""):
        cache_key = f"{key_words}_{page}_{time_s}_{time_e}"
        if self.enable_cache and cache_key in self.cache_dict.get("google_scholar", {}).get("kw_to_ids", {}):
            logger.info(f"从缓存加载论文")
            papers = []
            paper_ids = self.cache_dict["google_scholar"]["kw_to_ids"][cache_key]
            for paper_id in paper_ids:
                paper_path = self.paper_store_dir / f"{paper_id}.json"
                if paper_path.exists():
                    try:
                        with open(paper_path, "r", encoding="utf-8") as f:
                            paper_content = json.load(f)
                        papers.append(paper_content)
                    except Exception as e:
                        logger.warning(f"加载缓存论文失败: {e}")
            if len(papers) > 0:
                logger.debug(f"google_scholar: 从缓存中获取了 {len(papers)} 篇论文，关键词为 {key_words}")
                return papers
        
        if not SERPAPI_AVAILABLE or not SERPAPI_API_KEY:
            logger.error("SerpAPI未正确配置，无法进行Google Scholar搜索")
            return []
        
        papers = []
        page_count = int(page)
        results_per_page = 10 
        logger.error("SerpAPI正确配置，进行Google Scholar搜索")
        
        for current_page in range(page_count):
            search_params = {
                "q": key_words,
                "hl": "en",
                "start": current_page * results_per_page
            }
            
            if time_s:
                search_params["as_ylo"] = time_s
            if time_e:
                search_params["as_yhi"] = time_e
            
            logger.info(f"正在搜索第 {current_page + 1} 页，关键词: {key_words}")
            search_results = self._search_google_scholar_with_serpapi(search_params)
            
            if not search_results:
                logger.warning(f"无法获取第 {current_page + 1} 页的结果")
                break
            
            organic_results = search_results.get("organic_results", [])
            if not organic_results:
                logger.warning(f"在第 {current_page + 1} 页未找到论文结果")
                break
            
            page_papers = []
            for result in organic_results:
                paper = self._parse_serpapi_scholar_result(result)
                if paper:
                    papers.append(paper)
                    page_papers.append(paper)
                    
            logger.info(f"第 {current_page + 1} 页获取了 {len(page_papers)} 篇论文")

            pagination = search_results.get("pagination", {})
            if not pagination.get("next"):
                logger.info("已到达最后一页")
                break

            if current_page < page_count - 1:
                time.sleep(1.0)
        
        logger.info(f"google_scholar: 共获取了 {len(papers)} 篇论文，关键词为 {key_words}")

        if self.enable_cache and papers:
            paper_ids = []
            for paper in papers:
                file_id = paper["_id"]
                paper_ids.append(file_id)
                filename = sanitize_filename(f"{file_id}.json")
                paper_path = self.paper_store_dir / filename
                
                try:
                    save_result(json.dumps(paper, indent=4, ensure_ascii=False), paper_path)
                    
                    if 'title' in paper and paper['title']:
                        self.mapping_dict["google_scholar"]["title_to_id"][paper["title"]] = file_id
                        self.mapping_dict["google_scholar"]["id_to_title"][file_id] = paper["title"]
                except Exception as e:
                    logger.warning(f"保存论文失败: {e}")
            
            if "google_scholar" not in self.cache_dict:
                self.cache_dict["google_scholar"] = {"kw_to_ids": {}}
            self.cache_dict["google_scholar"]["kw_to_ids"][cache_key] = paper_ids
            
            try:
                save_result(json.dumps(self.cache_dict, indent=4, ensure_ascii=False), self.cache_file_path)
                save_result(json.dumps(self.mapping_dict, indent=4, ensure_ascii=False), self.mapping_file_path)
                logger.debug("缓存保存成功")
            except Exception as e:
                logger.warning(f"保存缓存失败: {e}")
        
        return papers
    
    def search_on_arxiv(self, key_words):
        key_words = key_words.split(",")
        id_counter = Counter()
        id2paper = {}
        for key_word in key_words:
            papers = self.search_on_arxiv_single_word(key_word)
            _ids = [paper["_id"] for paper in papers]
            id_counter.update(_ids)
            id2paper.update({paper["_id"] : paper for paper in papers})
            
        overlaped_papers = [paper for _id, paper in id2paper.items() if id_counter[_id] >= 1]
        logger.debug(f"搜索了 {len(id_counter)} 篇论文，关键词为 {key_words}，返回 {len(overlaped_papers)} 篇重叠度大于等于1的论文")
        return overlaped_papers
    
    def search_on_arxiv_single_word(self, key_word):
        if self.enable_cache and key_word in self.cache_dict["arxiv"]["kw_to_ids"]:
            logger.info(f"从缓存加载论文 {self.paper_store_dir}")
            papers = []
            paper_ids = self.cache_dict["arxiv"]["kw_to_ids"][key_word]
            for paper_id in paper_ids:
                paper = self._load_paper(paper_id)
                if paper:
                    papers.append(paper)
            if len(papers) > 0:
                return papers
        
        search_word = key_word.lower().strip()
        result_ids = set()
        
        for paper_id, metadata in self.papers_metadata.items():
            if metadata["from"] != "arxiv":
                continue
            title = metadata["title"].lower()
            abstract = metadata["abstract"].lower()
            if search_word in title or search_word in abstract:
                result_ids.add(paper_id)
            if len(result_ids) >= self.SINGLE_WORD_LIMIT:
                break
            
        papers = []
        for paper_id in result_ids:
            paper = self._load_paper(paper_id)
            if paper:
                if "from" not in paper:
                    paper["from"] = "arxiv"
                papers.append(paper)
                
        logger.debug(f"arxiv: 获取了 {len(papers)} 篇论文，关键词为 {key_word}")
        
        if self.enable_cache:
            self.cache_dict["arxiv"]["kw_to_ids"][key_word] = list(result_ids)
            for paper in papers:
                file_id = paper["_id"]
                file_title = paper.get("title", "")
                self.mapping_dict["arxiv"]["title_to_id"][file_title] = file_id
                self.mapping_dict["arxiv"]["id_to_title"][file_id] = file_title
                filename = file_id + ".json"
                filename = sanitize_filename(filename)
                paper_path = self.paper_store_dir / filename
                save_result(json.dumps(paper, indent=4), paper_path)
            
            save_result(json.dumps(self.cache_dict, indent=4), self.cache_file_path)
            save_result(json.dumps(self.mapping_dict, indent=4), self.mapping_file_path)
        
        return papers
    
    def search_by_title(self, title_query):
        title_query = title_query.lower()
        matching_papers = []
        for paper_id, metadata in self.papers_metadata.items():
            if title_query in metadata["title"].lower():
                paper = self._load_paper(paper_id)
                if paper:
                    matching_papers.append(paper)
                
        logger.debug(f"通过标题搜索获取了 {len(matching_papers)} 篇论文，查询词为 {title_query}")
        return matching_papers
    
    def get_all_papers(self, limit: int = None):
        papers = []
        count = 0
        for paper_id in self.papers_metadata:
            paper = self._load_paper(paper_id)
            if paper:
                papers.append(paper)
                count += 1
                if limit and count >= limit:
                    break
        logger.debug(f"获取了 {len(papers)} 篇论文")
        return papers