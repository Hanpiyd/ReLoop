import json
import os
import re
import time
import random
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
import urllib.parse
import xml.etree.ElementTree as ET
import requests
from tqdm import tqdm
import logging

from src.configs.config import(
    CACHE_DIR,
    OUTPUT_DIR,
    DATASET_DIR,
    PAPERS_DIR,
    TASK_DIRS
)

from src.modules.utils import sanitize_filename, save_result
from src.configs.utils import ensure_task_dirs

logger = logging.getLogger(__name__)

class ArxivCrawler:
    ARXIV_APR_URL = "http://export.arxiv.org/api/query"
    MAX_RESULTS_PER_REQUEST = 100
    NAMESPACES = {
        'atom': 'http://www.w3.org/2005/Atom',
        'arxiv': 'http://arxiv.org/schemas/atom',
        'opensearch': 'http://a9.com/-/spec/opensearch/1.1/'
    }

    def __init__(self, task_id: str = None, output_dir: str = None):
        if task_id is not None:
            self.task_dir = ensure_task_dirs(task_id)
            self.output_dir = self.task_dir / TASK_DIRS["PAPERS_DIR"]
        elif output_dir is not None:
            self.output_dir = Path(output_dir)
            self.output_dir.mkdir(parents=True, exist_ok=True)
        else:
            self.output_dir = Path(PAPERS_DIR)
            self.output_dir.mkdir(parents=True, exist_ok=True)

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        })

    def _normalize_arxiv_id(self, arxiv_id):
        if arxiv_id.startswith("http://arxiv.org/abs/") or arxiv_id.startswith("https://arxiv.org/abs/"):
            arxiv_id = arxiv_id.split("/abs/")[-1]
        if "v" in arxiv_id and arxiv_id[0].isdigit():
            arxiv_id = arxiv_id.split("v")[0]
        return arxiv_id.strip()

    def _parse_arxiv_entry(self, entry):
        try:
            id_elem = entry.find(".//atom:id", self.NAMESPACES)
            if id_elem is None or id_elem.text is None:
                return None
            arxiv_id = id_elem.text
            arxiv_id = self._normalize_arxiv_id(arxiv_id)
            title_elem = entry.find('.//atom:title', self.NAMESPACES)
            if title_elem is None or title_elem.text is None:
                return None
            title = title_elem.text.strip()
            abstract_elem = entry.find('.//atom:summary', self.NAMESPACES)
            abstract = abstract_elem.text.strip() if abstract_elem is not None and abstract_elem.text is not None else ""
            published_elem = entry.find('.//atom:published', self.NAMESPACES)
            published = published_elem.text if published_elem is not None and published_elem.text is not None else ""
            updated_elem = entry.find('.//atom:updated', self.NAMESPACES)
            updated = updated_elem.text if updated_elem is not None and updated_elem.text is not None else ""
            authors = []
            for author in entry.findall(".//atom:author", self.NAMESPACES):
                name_elem = author.find('.//atom:name', self.NAMESPACES)
                if name_elem is not None and name_elem.text is not None:
                    authors.append(name_elem.text)
            categories = []
            primary_category = entry.find('.//arxiv:primary_category', self.NAMESPACES)
            if primary_category is not None and 'term' in primary_category.attrib:
                categories.append(primary_category.get('term'))
            for category in entry.findall('.//atom:category', self.NAMESPACES):
                cat = category.get('term')
                if cat and cat not in categories:
                    categories.append(cat)
            links = {}
            for link in entry.findall('.//atom:link', self.NAMESPACES):
                rel = link.get('rel')
                href = link.get('href')
                if rel and href:
                    links[rel] = href
            md_text = f"# {title}\n\n## Authors\n{', '.join(authors)}\n\n## Abstract\n{abstract}\n\n"
            md_text += f"## Categories\n{', '.join(categories)}\n\n"
            md_text += f"## Publication Date\n{published}\n\n"
            md_text += f"## Links\nPaper: {links.get('alternate', '')}\nPDF: {links.get('related', '')}\n"
            paper = {
                "_id": arxiv_id,
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "author_string": ", ".join(authors),
                "categories": categories,
                "primary_category": categories[0] if categories else "",
                "published": published,
                "updated": updated,
                "links": links,
                "detail_url": links.get('alternate', f"https://arxiv.org/abs/{arxiv_id}"),
                "pdf_url": links.get('related', f"https://arxiv.org/pdf/{arxiv_id}.pdf"),
                "from": "arxiv",
                "fetched_at": int(time.time()),
                "md_text": md_text
            }
            try:
                first_author = authors[0].split()[-1] if authors else None
                year = published.split('-')[0] if published else "XXXX"
                bib_name = f"{first_author}{year}{title.split()[0]}".lower()
                bib_name = re.sub(r'[^\w]', '', bib_name)
                reference = f"@article{{{bib_name},\n"
                reference += f"  title={{{title}}},\n"
                reference += f"  author={{{' and '.join(authors)}}},\n"
                reference += f"  journal={{arXiv preprint arXiv:{arxiv_id}}},\n"
                reference += f"  year={{{year}}},\n"
                reference += f"  url={{https://arxiv.org/abs/{arxiv_id}}}\n"
                reference += "}"
                paper["reference"] = reference
                paper["bib_name"] = bib_name
            except:
                pass
            return paper
        except:
            return None

    def _make_arxiv_api_request(self, params):
        try:
            time.sleep(random.uniform(1.0, 3.0))
            response = self.session.get(self.ARXIV_APR_URL, params=params, timeout=30)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            return root
        except:
            return None

    def fetch_papers(self, search_query: str, max_results: int = 100):
        all_papers = []
        saved_paths = []
        for start in range(0, max_results, self.MAX_RESULTS_PER_REQUEST):
            current_max = min(self.MAX_RESULTS_PER_REQUEST, max_results - start)
            params = {
                'search_query': search_query,
                'start': start,
                'max_results': current_max,
                'sortBy': 'submittedDate',
                'sortOrder': 'ascending'
            }
            root = self._make_arxiv_api_request(params)
            if not root:
                continue
            entries = root.findall('.//atom:entry', self.NAMESPACES)
            if not entries:
                break
            for entry in entries:
                paper = self._parse_arxiv_entry(entry)
                if paper:
                    file_id = paper["_id"]
                    if not file_id or file_id == "arxi":
                        import uuid
                        file_id = f"paper_{str(uuid.uuid4())[:8]}"
                        paper["_id"] = file_id
                    filename = f"{file_id}.json"
                    filename = sanitize_filename(filename)
                    file_path = self.output_dir / filename
                    if file_path.exists():
                        filename = f"{file_id}_{int(time.time())}.json"
                        file_path = self.output_dir / filename
                    try:
                        save_result(json.dumps(paper, indent=4), file_path)
                        saved_paths.append(str(file_path))
                    except:
                        continue
                    all_papers.append(paper)
            if len(entries) < current_max:
                break
            time.sleep(random.uniform(3.0, 5.0))
        return all_papers, saved_paths

    def fetch_by_date_range(self, start_date: str, end_date: str, subject_category: str = None, max_results: int = 1000):
        from dateutil.parser import parse as parse_date
        import pytz
        utc = pytz.UTC
        try:
            start_date_obj = utc.localize(datetime.datetime.strptime(start_date, '%Y-%m-%d'))
            end_date_obj = utc.localize(datetime.datetime.strptime(end_date, '%Y-%m-%d'))
        except:
            return [], []
        if subject_category:
            if '.' in subject_category:
                search_query = f"cat:{subject_category}"
            else:
                search_query = f"cat:{subject_category}.*"
        else:
            search_query = "all"
        papers, saved_paths = self.fetch_papers(search_query, max_results)
        filtered_papers = []
        filtered_paths = []
        for paper, path in zip(papers, saved_paths):
            try:
                pub_date = parse_date(paper.get("published", ""))
                if pub_date.tzinfo is None:
                    pub_date = utc.localize(pub_date)
                if start_date_obj <= pub_date <= end_date_obj:
                    filtered_papers.append(paper)
                    filtered_paths.append(path)
            except:
                continue
        return filtered_papers, filtered_paths

    def fetch_by_keywords(self, keywords: List[str], categories: List[str], max_results_per_query: int = 20):
        all_papers = []
        all_paths = []
        for category in categories:
            for keyword in keywords:
                search_query = f"ti:{keyword.replace(' ', '+')} AND cat:{category}"
                papers, paths = self.fetch_papers(search_query, max_results_per_query)
                all_papers.extend(papers)
                all_paths.extend(paths)
                time.sleep(random.uniform(2.0, 4.0))
        return all_papers, all_paths

    def get_arxiv_categories(self):
        categories = [
            {"code": "cs", "description": "Computer Science"},
            {"code": "math", "description": "Mathematics"},
            {"code": "physics", "description": "Physics"},
            {"code": "q-bio", "description": "Quantitative Biology"},
            {"code": "q-fin", "description": "Quantitative Finance"},
            {"code": "stat", "description": "Statistics"},
            {"code": "econ", "description": "Economics"},
            {"code": "eess", "description": "Electrical Engineering and Systems Science"},
            {"code": "cond-mat", "description": "Condensed Matter"},
            {"code": "hep", "description": "High Energy Physics"},
            {"code": "astro-ph", "description": "Astrophysics"},
            {"code": "nucl", "description": "Nuclear"},
            {"code": "gr-qc", "description": "General Relativity and Quantum Cosmology"},
            {"code": "quant-ph", "description": "Quantum Physics"},
            {"code": "nlin", "description": "Nonlinear Sciences"},
        ]
        cs_subcategories = [
            {"code": "cs.AI", "description": "Artificial Intelligence"},
            {"code": "cs.CL", "description": "Computation and Language"},
            {"code": "cs.CV", "description": "Computer Vision and Pattern Recognition"},
            {"code": "cs.LG", "description": "Machine Learning"},
            {"code": "cs.NE", "description": "Neural and Evolutionary Computing"},
            {"code": "cs.RO", "description": "Robotics"},
            {"code": "cs.IR", "description": "Information Retrieval"},
            {"code": "cs.SE", "description": "Software Engineering"},
            {"code": "cs.CY", "description": "Computers and Society"},
            {"code": "cs.CR", "description": "Cryptography and Security"},
            {"code": "cs.DB", "description": "Databases"},
            {"code": "cs.DC", "description": "Distributed, Parallel, and Cluster Computing"},
            {"code": "cs.DS", "description": "Data Structures and Algorithms"},
            {"code": "cs.HC", "description": "Human-Computer Interaction"},
            {"code": "cs.NA", "description": "Numerical Analysis"},
            {"code": "cs.PL", "description": "Programming Languages"},
        ]
        return categories + cs_subcategories

