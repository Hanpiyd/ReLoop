import subprocess
import os
import re
import time
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter
import json
import logging
from src.LLM.EmbedAgent import EmbedAgent
from sklearn.metrics.pairwise import cosine_distances
from src.modules.utils import save_result

logger = logging.getLogger(__name__)

def parse_pdf(pdf_path, output_dir="./mineru_output"):
    os.makedirs(output_dir, exist_ok=True)
    cmd = ["mineru", "-p", pdf_path, "-o", output_dir, "-m", "auto", "--lang", "en"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        logger.info(f"成功处理PDF: {pdf_path}")
        logger.debug(f"输出目录: {output_dir}")
        if result.stdout:
            logger.debug(f"输出信息: {result.stdout}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"处理PDF失败: {pdf_path}, 错误: {e}")
        return False

def extract_info(pdf_path, output_dir="./mineru_output"):
    file_dir = Path(output_dir) / Path(pdf_path).stem / "auto" / f"{Path(pdf_path).stem}.md"
    if not file_dir.exists():
        logger.error(f"无法找到解析后的MD文件: {file_dir}")
        return {"title": "", "abstract": "", "cited_references": {}}
    
    with open(file_dir, "r", encoding="utf-8") as f:
        content = f.read()

    title = extract_title(content)
    abstract = extract_abstract(content)
    cited_references = extract_cited_references(content)
    authors = extract_authors(content, title)
    year = extract_year(content, pdf_path)

    return {
        "title": title,
        "abstract": abstract,
        "cited_references": cited_references,
        "authors": authors,
        "year": year
    }

def extract_title(content):
    title_patterns = [
        r'^\s*#\s*(.*?)$',                            
        r'(?i)^\s*(.+?)\n\s*(?:abstract|introduction)', 
        r'(?i)<title>(.*?)</title>',                   
        r'^\s*([A-Z][^.!?]*?(?::|$))\s*\n',
        r'^\s*([A-Z][A-Z0-9\s\-:,&]+)\s*\n',
        r'(?i)^([^\n]{5,150})\n\s*(?:by|authors?|submitted by)',
        r'^((?:[A-Z][a-z]*[A-Za-z0-9\s\-:,&]+){1,150})\n\s*\n',
        r'^\s*([^\n]{10,150})\n\s*[^\n]{0,20}\n\s*\d{1,2}\s*[A-Za-z]+\s*\d{4}'
    ]

    for pattern in title_patterns:
        matches = re.finditer(pattern, content, re.MULTILINE)
        for match in matches:
            title = match.group(1).strip()
            if title and len(title) < 200 and len(title) > 5:
                if not re.search(r'(?i)(submitted to|university|conference|journal|proceedings|vol\.|\bpp\.|\bissn\b|\bisbn\b|©|copyright)', title):
                    return title
    
    first_lines = content.split('\n')[:5]
    for line in first_lines:
        line = line.strip()
        if line and len(line) < 200 and len(line) > 10:
            if not line.startswith('#') and not re.search(r'(?i)(submitted to|university|conference|journal|proceedings)', line):
                return line.replace("#", "").strip()
    
    for line in content.split('\n'):
        line = line.strip()
        if line and len(line) < 200:
            return line.replace("#", "").strip()
    
    return ""

def extract_abstract(content):
    abstract_patterns = [
        r'(?i)^\s*#*\s*ABSTRACT\s*\n+(.*?)(?=\n\s*#|\n\s*\d|\n\s*KEYWORDS|\n\s*$)',
        r'(?i)abstract[:\.\-—–]?\s*(.*?)(?=\n\s*\d|\n\s*[A-Z]{2,}|\n\s*keywords:|\n\s*$)',
        r'(?i)^abstract\s*$(.*?)(?=\n\n)',
        r'(?i)<abstract>\s*(.*?)\s*</abstract>',                               
        r'(?i)(?:^|\n)abstract(?:\s*\n+\s*)((?:.+?\n)+?)(?:\n\n|\n#|\n\d)',    
        r'(?i)abstract[:\.\-—–]?\s*((?:.+?\n)+?)(?=\n\s*(?:keywords|introduction|#|$))', 
        r'(?i)(?<=\n\n)([^\n]{50,}(?:\n[^\n]{20,}){0,5})(?=\n\n)',
        r'(?i)(?<=^|\n)(?:summary|overview)[:\.\-—–]?\s*((?:.+?\n)+?)(?=\n\s*(?:keywords|introduction|#|\d|\n\n|$))',
        r'(?i)\n[^\n]*abstract[^\n]*\n+([^\n]+(?:\n[^\n]+){1,10})(?=\n\n)',
        r'(?i)\n\d+\.\s*abstract\s*\n+([^\n]+(?:\n[^\n]+){1,10})(?=\n\n)'
    ]
    
    for pattern in abstract_patterns:
        match = re.search(pattern, content, re.DOTALL | re.MULTILINE)
        if match:
            abstract_text = match.group(1).strip()
            abstract_text = re.sub(r'\n+', ' ', abstract_text)
            abstract_text = re.sub(r'\s{2,}', ' ', abstract_text)
            if len(abstract_text) > 50:
                return abstract_text
    
    title_match = re.search(r'^\s*#\s*(.*?)$', content, re.MULTILINE)
    if title_match:
        title_end_pos = title_match.end()
        next_text = content[title_end_pos:title_end_pos+5000]  
        
        first_para_match = re.search(r'\n\n([^\n]{100,}(?:\n[^\n]+){0,5})\n\n', next_text)
        if first_para_match:
            abstract_text = first_para_match.group(1).strip()
            abstract_text = re.sub(r'\n+', ' ', abstract_text)
            abstract_text = re.sub(r'\s{2,}', ' ', abstract_text)
            if len(abstract_text) > 100 and not re.search(r'(?i)(keywords:|introduction:|figure \d|table \d)', abstract_text):
                return abstract_text
                
    return ""

def extract_cited_references(content):
    reference_section_patterns = [
        r'(?i)(?:^|\n)(?:##?\s*)?(?:references|bibliography|cited\s+(?:works|papers)|works\s+cited|引用文献)\s*(?:\n|$)(.*?)(?:\n\s*#|\n\s*$)',
        r'(?i)(?:^|\n)(?:#|\d+\.)\s*(?:references|bibliography|cited\s+(?:works|papers)|works\s+cited)\s*(?:\n|$)(.*?)(?:\n\s*#|\n\s*$)',
    ]
    
    reference_section = None
    for pattern in reference_section_patterns:
        match = re.search(pattern, content, re.DOTALL)
        if match:
            reference_section = match.group(1).strip()
            break
    
    if not reference_section:
        main_section_pattern = r'(?i)^\s*#\s*\d*\.?\s*(?:RELATED|LITERATURE|PREVIOUS)\s+WORK\s*$'
        main_section_match = re.search(main_section_pattern, content, re.MULTILINE)
        if main_section_match:
            main_section_start = main_section_match.start()
            next_main_section_pattern = r'(?i)^\s*#\s*(?:[3-9]|[1-9]\d+)\.?\s+'
            next_main_match = re.search(next_main_section_pattern, content[main_section_start:], re.MULTILINE)
            if next_main_match:
                main_section_end = main_section_start + next_main_match.start()
            else:
                main_section_end = len(content)
            reference_section = content[main_section_start:main_section_end].strip()
        else:
            subsection_pattern = r'(?i)^\s*#\s*\d+\.\d+\.?\s+'
            subsection_matches = list(re.finditer(subsection_pattern, content, re.MULTILINE))
            if subsection_matches:
                first_subsection_start = subsection_matches[0].start()
                content_before = content[:first_subsection_start]
                related_work_title = re.search(r'(?i)(?:RELATED|LITERATURE|PREVIOUS)\s+WORK', content_before)
                if related_work_title:
                    main_section_start = related_work_title.start()
                else:
                    main_section_start = max(0, first_subsection_start - 200)
                next_main_pattern = r'(?i)^\s*#\s*(?:[3-9]|[1-9]\d+)\.?\s+'
                next_main_match = re.search(next_main_pattern, content[first_subsection_start:], re.MULTILINE)
                if next_main_match:
                    main_section_end = first_subsection_start + next_main_match.start()
                else:
                    main_section_end = len(content)
                reference_section = content[main_section_start:main_section_end].strip()
            else:
                reference_section = content

    citation_patterns = [
        r'\[(\d+(?:,\s*\d+)*)\]', 
        r'\[(\d+)[-–]\s*(\d+)\]',
        r'\(([A-Za-z]+\s+et\s+al\.,?\s+\d{4}[a-z]?)\)',
        r'\(([A-Za-z]+\s+and\s+[A-Za-z]+,?\s+\d{4}[a-z]?)\)',
        r'\(([A-Za-z]+,?\s+\d{4}[a-z]?)\)',
        r'(?<!\w)(\d+)\.\s+([A-Za-z].*?\d{4})' 
    ]

    cited_ids = set()
    for pattern in citation_patterns:
        matches = re.finditer(pattern, content)
        for match in matches:
            if len(match.groups()) == 1: 
                citation = match.group(1)
                if ',' in citation:
                    for num in re.findall(r'\d+', citation):
                        cited_ids.add(num.strip())
                else:
                    cited_ids.add(citation.strip())
            elif len(match.groups()) == 2 and re.match(r'\d+', match.group(1)) and re.match(r'\d+', match.group(2)):
                try:
                    start = int(match.group(1))
                    end = int(match.group(2))
                    for num in range(start, end + 1):
                        cited_ids.add(str(num))
                except ValueError:
                    continue

    references = {}
    
    for ref_id in [id for id in cited_ids if re.match(r'^\d+$', id)]:
        ref_patterns = [
            rf'\[\s*{ref_id}\s*\]\s+(.*?)(?=\s*\[\s*\d+\s*\]|\s*$)',
            rf'^\s*{ref_id}\.\s+(.*?)(?=^\s*\d+\.|\s*$)',
            rf'(?<=\n){ref_id}\.\s+(.*?)(?=\n\d+\.|\s*$)'
        ]
        
        for pattern in ref_patterns:
            matches = list(re.finditer(pattern, content, re.DOTALL | re.MULTILINE))
            if matches:
                ref_text = matches[-1].group(1).strip()
                references[ref_id] = ref_text
                break
        
        if ref_id not in references:
            alt_pattern = rf'\[\s*{ref_id}\s*\][^[]*'
            alt_matches = re.findall(alt_pattern, content)
            if alt_matches:
                ref_text = alt_matches[-1].replace(f'[{ref_id}]', '').strip()
                references[ref_id] = ref_text
    
    author_year_refs = [id for id in cited_ids if not re.match(r'^\d+$', id)]
    if author_year_refs and reference_section:
        lines = reference_section.split('\n')
        for i, line in enumerate(lines):
            for ref_id in author_year_refs:
                if ref_id in line:
                    ref_text = line
                    j = i + 1
                    while j < len(lines) and not any(r in lines[j] for r in author_year_refs) and len(lines[j].strip()) > 0:
                        ref_text += " " + lines[j].strip()
                        j += 1
                    references[ref_id] = ref_text
                    break
    
    return references

def extract_authors(content, title):
    authors = []
    if title:
        title_pattern = re.escape(title)
        after_title = re.search(f"{title_pattern}(.*?)(?:abstract|introduction)", content, re.IGNORECASE | re.DOTALL)
        if after_title:
            author_section = after_title.group(1)
            
            author_line_pattern = r'([A-Z][a-z]+(?:\s+[A-Z]\.)+\s+[A-Z][a-z]+|[A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)'
            authors_matches = re.finditer(author_line_pattern, author_section)
            for match in authors_matches:
                authors.append(match.group(1).strip())
            
            if not authors:
                email_pattern = r'([A-Z][a-z]+\s+[A-Z][a-z]+)(?:.*?)([a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})'
                email_matches = re.finditer(email_pattern, author_section, re.IGNORECASE)
                for match in email_matches:
                    authors.append(match.group(1).strip())
            
            if not authors:
                affiliation_pattern = r'([A-Z][a-z]+\s+[A-Z][a-z]+)(?:,|\s+and|\s*\n)(?:.*?)(?:University|Institute|College|Corporation|Laboratory)'
                affiliation_matches = re.finditer(affiliation_pattern, author_section, re.IGNORECASE)
                for match in affiliation_matches:
                    authors.append(match.group(1).strip())

    if not authors:
        author_patterns = [
            r'^\s*(?:Authors?|By):\s*([^,\n]+(?:,\s*[^,\n]+)*)',
            r'(?<=\n\n)([A-Z][a-z]+\s+[A-Z][a-z]+(?:,\s*[A-Z][a-z]+\s+[A-Z][a-z]+)*)',
        ]
        
        for pattern in author_patterns:
            match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)
            if match:
                author_text = match.group(1)
                author_parts = re.split(r',\s*|\s+and\s+|;\s*', author_text)
                for part in author_parts:
                    part = part.strip()
                    if part and re.match(r'[A-Z][a-z]+\s+[A-Z][a-z]+', part):
                        authors.append(part)
    
    unique_authors = []
    for author in authors:
        author = author.strip()
        if re.match(r'[A-Z][a-z]+\s+[A-Z][a-z]+', author) and author not in unique_authors:
            unique_authors.append(author)
    
    return unique_authors if unique_authors else ["Unknown Author"]

def extract_year(content, pdf_path):
    year_patterns = [
        r'(?:©|\(c\)|\(C\)|Copyright)?\s*(\d{4})',
        r'(?:Proceedings|Conference)\s+(?:of|on).*?(\d{4})',
        r'(?:Received|Submitted|Accepted|Published).*?(\d{4})',
        r'(?<=\n)(?:[A-Za-z]+\s+)?(\d{4})(?=$|\n)',
    ]
    
    for pattern in year_patterns:
        match = re.search(pattern, content)
        if match:
            year = match.group(1)
            if 1900 <= int(year) <= 2030: 
                return year
    
    arxiv_match = re.search(r'(\d{4}\.\d{5})', pdf_path)
    if arxiv_match:
        year = "20" + arxiv_match.group(1)[:2]
        return year

    return str(time.strftime("%Y"))
              
def download_pdf_file(cited_references, download_dir="./test"):
    os.makedirs(download_dir, exist_ok=True)

    session = requests.Session()
    adapter = HTTPAdapter(
        pool_connections=10,
        pool_maxsize=10,
        max_retries=3
    )
    session.mount('http://', adapter)
    session.mount('https://', adapter)

    arxiv_ids = []
    file_paths = []
    for key, value in cited_references.items():
        matches = re.findall(r'arXiv:(\d{4}\.\d{5})', value)
        if matches:
            arxiv_ids.append(matches[0])
    
    try:
        for arxiv_id in arxiv_ids:
            pdf_url = f"http://arxiv.org/pdf/{arxiv_id}.pdf"
            file_path = Path(download_dir) / f"{arxiv_id}.pdf"
            if file_path.exists():
                logger.info(f"文件已存在，跳过下载: {arxiv_id}")
                file_paths.append(str(file_path))
                continue
            try:
                response = session.get(pdf_url, timeout=30)
                if response.status_code == 200:
                    file_paths.append(str(file_path))
                    with open(file_path, "wb") as f:
                        f.write(response.content)
                    logger.info(f"下载成功: {arxiv_id}")
                else:
                    logger.warning(f"下载失败: {arxiv_id}, 状态码: {response.status_code}")
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"下载过程中出错: {arxiv_id}, 错误: {e}")

    finally:
        session.close()
    
    return file_paths

def filter_paper(pdf_path, topic, embed_agent=None, output_dir="./mineru_output", threshold=0.5):
    if embed_agent is None:
        embed_agent = EmbedAgent()
        
    if not parse_pdf(pdf_path, output_dir):
        return False
        
    paper_info = extract_info(pdf_path, output_dir)
    title = paper_info["title"]
    abstract = paper_info["abstract"]
    
    if not title or not abstract:
        logger.warning(f"无法提取足够信息来评估相关性: {pdf_path}")
        return False
        
    content = f"{title.lower()} {abstract.lower()}"
    
    try:
        paper_embedding = embed_agent.remote_embed(content)
        topic_embedding = embed_agent.remote_embed(topic)
        
        if not paper_embedding or not topic_embedding:
            logger.warning(f"无法生成嵌入向量: {pdf_path}")
            return True        
        distance = cosine_distances([paper_embedding], [topic_embedding])[0][0]
        logger.debug(f"论文 '{title}' 与主题的相似度距离: {distance}")
        return distance <= threshold 
    except Exception as e:
        logger.error(f"计算相似度时出错: {pdf_path}, 错误: {e}")
        return True
    
def search_iteratively(pdf_paths, topic, output_dir="./mineru_output", download_dir="./test", embed_agent=None, threshold=0.5):
    if embed_agent is None:
        embed_agent = EmbedAgent()
        
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(download_dir, exist_ok=True)
    
    paper_dir = [] 
    transitional_dir = [] 
    result_dir = []  
    
    for pdf_path in pdf_paths:
        if not os.path.exists(pdf_path):
            logger.warning(f"PDF文件不存在: {pdf_path}")
            continue
            
        logger.info(f"处理初始PDF: {pdf_path}")
        paper_dir.append(pdf_path)
        
        if not parse_pdf(pdf_path, output_dir):
            continue
            
        paper_info = extract_info(pdf_path, output_dir)
        file_paths = download_pdf_file(paper_info["cited_references"], download_dir)
        
        for file_path in file_paths:
            if file_path not in paper_dir and filter_paper(file_path, topic, embed_agent, output_dir, threshold):
                transitional_dir.append(file_path)
                paper_dir.append(file_path)
                logger.info(f"添加一级引用论文: {file_path}")

    for pdf_path in transitional_dir:
        logger.info(f"处理一级引用论文: {pdf_path}")
        
        if not parse_pdf(pdf_path, output_dir):
            continue
            
        paper_info = extract_info(pdf_path, output_dir)
        file_paths = download_pdf_file(paper_info["cited_references"], download_dir)
        
        for file_path in file_paths:
            if file_path not in paper_dir and filter_paper(file_path, topic, embed_agent, output_dir, threshold):
                result_dir.append(file_path)
                paper_dir.append(file_path)
                logger.info(f"添加二级引用论文: {file_path}")

    return paper_dir, result_dir

def create_paper_data_from_pdf(pdf_path, output_dir="./mineru_output"):
    if not parse_pdf(pdf_path, output_dir):
        logger.error(f"无法解析PDF: {pdf_path}")
        return None
        
    file_path = Path(output_dir) / Path(pdf_path).stem / "auto" / f"{Path(pdf_path).stem}.md"
    if not file_path.exists():
        logger.error(f"无法找到解析后的MD文件: {file_path}")
        return None
        
    with open(file_path, "r", encoding="utf-8") as f:
        full_content = f.read()
        
    paper_info = extract_info(pdf_path, output_dir)
    title = paper_info["title"] if paper_info["title"] else Path(pdf_path).stem
    abstract = paper_info["abstract"]
    authors = paper_info["authors"]
    year = paper_info["year"]

    keywords = []
    keywords_patterns = [
        r'(?i)^\s*#*\s*KEYWORDS\s*\n+(.*?)(?=\n\s*#|\n\s*\d|\n\s*$)',
        r'(?i)keywords[:\.\-—–]?\s*(.*?)(?=\n\s*\d|\n\s*[A-Z]{2,}|\n\s*$)',
        r'(?i)index terms[:\.\-—–]?\s*(.*?)(?=\n\s*\d|\n\s*[A-Z]{2,}|\n\s*$)'
    ]
    
    for pattern in keywords_patterns:
        keywords_match = re.search(pattern, full_content, re.DOTALL | re.MULTILINE)
        if keywords_match:
            keywords_text = keywords_match.group(1).strip()
            keywords = [k.strip() for k in re.split(r'[,;]', keywords_text)]
            break

    arxiv_match = re.search(r'(\d{4}\.\d{5})', pdf_path)
    if arxiv_match:
        paper_id = f"arxiv_{arxiv_match.group(1)}"
    else:
        sanitized_title = re.sub(r'[^\w]', '', title.lower())[:15]
        paper_id = f"pdf_{sanitized_title}_{int(time.time())}"

    first_author = authors[0].split()[-1] if authors else "Unknown"
    first_author = re.sub(r'[^\w]', '', first_author.lower())
    title_first_word = re.sub(r'[^\w]', '', title.split()[0].lower()) if title else ""
    bib_name = f"{first_author}{year}{title_first_word}"

    author_str = " and ".join(authors) if isinstance(authors, list) else authors
    if arxiv_match:
        arxiv_id = arxiv_match.group(1)
        reference = f"@article{{{bib_name},\n  title={{{title}}},\n  author={{{author_str}}},\n"
        reference += f"  journal={{arXiv preprint arXiv:{arxiv_id}}},\n  year={{{year}}},\n"
        reference += f"  url={{https://arxiv.org/abs/{arxiv_id}}}\n}}"
    else:
        reference = f"@article{{{bib_name},\n  title={{{title}}},\n  author={{{author_str}}},\n"
        reference += f"  year={{{year}}}\n}}"

    md_text = f"# {title}\n\n## Authors\n{', '.join(authors)}\n\n## Abstract\n{abstract}\n\n"
    if keywords:
        md_text += f"## Keywords\n{', '.join(keywords)}\n\n"
        
    md_text += f"## Publication Date\n{year}\n\n"

    if arxiv_match:
        arxiv_id = arxiv_match.group(1)
        md_text += f"## Links\nPaper: https://arxiv.org/abs/{arxiv_id}\nPDF: https://arxiv.org/pdf/{arxiv_id}.pdf\n"
    else:
        md_text += f"## Links\nPDF: file://{os.path.abspath(pdf_path)}\n"

    return {
        "_id": paper_id,
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "author_string": ", ".join(authors),
        "year": year,
        "published": year,
        "bib_name": bib_name,
        "reference": reference,
        "md_text": md_text,
        "from": "pdf",
        "fetched_at": int(time.time()),
        "detail_url": f"file://{os.path.abspath(pdf_path)}",
        "pdf_path": os.path.abspath(pdf_path),
        "keywords": keywords
    }

def process_pdf_files_with_mineru(pdf_paths, topic, output_dir="./mineru_output", download_dir="./test"):
    if not pdf_paths:
        return []
    logger.info(f"使用mineru处理 {len(pdf_paths)} 个PDF文件...")
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(download_dir, exist_ok=True)
    embed_agent = EmbedAgent()
    all_paper_paths, _ = search_iteratively(
        pdf_paths=pdf_paths,
        topic=topic,
        output_dir=output_dir,
        download_dir=download_dir,
        embed_agent=embed_agent,
        threshold=0.3
    )

    processed_papers = []
    for pdf_path in all_paper_paths:
        paper_data = create_paper_data_from_pdf(pdf_path, output_dir)
        if paper_data:
            processed_papers.append(paper_data)
            logger.info(f"成功处理论文: {paper_data.get('title', '未知标题')}")
        else:
            logger.warning(f"无法提取论文数据: {pdf_path}")
    
    logger.info(f"成功处理 {len(processed_papers)} 篇论文")
    return processed_papers


def show_info(pdf_paths, output_dir="./mineru_output"):
    result_list = []
    for pdf_path in pdf_paths:
        tmp_result = extract_info(pdf_path=pdf_path, output_dir=output_dir)
        result_list.append({
            "title": tmp_result["title"],
            "abstract": tmp_result["abstract"],
            "authors": tmp_result["authors"],
            "year": tmp_result["year"]
        })
    return result_list
    

if __name__ == "__main__":
    dict = extract_info("2506.20274v1.pdf")
    file_paths = download_pdf_file(dict["cited_references"])
    print(file_paths)