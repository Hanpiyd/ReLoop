import os
import json
import re
import ast
from collections import defaultdict
from typing import List, Tuple
from itertools import combinations
from difflib import SequenceMatcher
import logging
from src.configs.config import ADVANCED_CHATAGENT_MODEL
from src.modules.utils import (
    load_meta_data,
    load_prompt,
)
from src.configs.config import BASE_DIR
from src.LLM.ChatAgent import ChatAgent

logger = logging.getLogger(__name__)


class LatexBaseTableBuilder:
    def __init__(self, chat_agent: ChatAgent = None):
        self.chat_agent = chat_agent if chat_agent is not None else ChatAgent()

    def clear_json_file(self, file_path):
        with open(file_path, "w") as f:
            json.dump([], f)

    def is_the_row_good(self, row: str, splitter: str = "&"):
        elements = row.strip().split(splitter)
        unexpected_element_count = 0
        for one in elements:
            if one.strip() in ["-", ""]:
                unexpected_element_count += 1
        if unexpected_element_count >= 2:
            return False
        return True

    def cite_name_match(self, data_list: List, cite_name: str) -> Tuple:
        for data in data_list:
            if (
                data["bib_name"] == cite_name
                and data["paper_type"] == "method"
                and data["attri"] is not None
                and len(data["attri"]["method"]["method abbreviation"].split()) < 2
            ):
                method_steps = data["attri"]["method"].get("method steps", "未提供方法步骤")
                content = (
                    "method name:"
                    + data["attri"]["method"]["method name"]
                    + "\n"
                    + "method_step: \n "
                    + str(method_steps)
                )
                complete_info = str(data["attri"])
                return (
                    complete_info,
                    content,
                    data["title"],
                    data["attri"]["method"]["method name"],
                    data["attri"]["method"]["method abbreviation"],
                    data["bib_name"],
                )
        return None, None, None, None, None, None

    def cite_name_match_count(self, data_list, cite_names):
        count = 0
        for cite_name in cite_names:
            if any(
                data["bib_name"] == cite_name and data["paper_type"] == "method"
                for data in data_list
            ):
                count += 1
        return count

    def cite_name_match_benchmark(self, data_list: List, cite_name):
        info = {}
        for data in data_list:
            if (
                data["bib_name"] == cite_name
                and data["paper_type"] == "benchmark"
                and data["attri"] is not None
                and len(data["attri"]["idea"]["benchmark abbreviation"].split()) < 2
                and self.convert_to_number(data["attri"]["dataset"]["size"]) is not None
                and self.convert_to_number(data["attri"]["dataset"]["size"]) < 10000000
            ):
                info["size"] = data["attri"]["dataset"]["size"]
                info["domain"] = data["attri"]["dataset"]["domain"]
                info["task format"] = data["attri"]["dataset"]["task format"]
                info["metric"] = data["attri"]["metrics"]["metric name"]
                info["bib_name"] = data["bib_name"]
                info["name"] = data["attri"]["idea"]["benchmark abbreviation"]
                return info
        return None

    def extract_attributes(self, file_content, pri_attribute):
        primary_pattern = re.compile(
            r"\[Attribute:\s*(.*?)\]", re.DOTALL
        ) 
        description_pattern = re.compile(
            r"\[Description:\s*(.*?)\]", re.DOTALL
        ) 

        primary_match = primary_pattern.search(file_content)
        description_match = description_pattern.search(file_content)

        attribute_name = None
        description_text = None

        if primary_match:
            attribute_name = primary_match.group(1)

        if description_match:
            description_text = description_match.group(1)

        if attribute_name is None or description_text is None:
            return None
        result = {
            "Primary Attribute Name": pri_attribute,
            "Secondary Attribute Name": attribute_name,
            "Description": description_text,
        }
        return result

    def save_attributes(self, attribute_name, description, file_name, type):
        directory = os.path.dirname(file_name)
        if directory and not os.path.exists(directory):
            os.makedirs(directory)
        if not os.path.exists(file_name):
            data = []
        else:
            with open(file_name, "r") as file:
                data = json.load(file)
        if type == 0:
            new_entry = {
                "Primary Attribute Name": attribute_name,
                "Description": description,
            }
            exists = any(
                item.get("Primary Attribute Name") == attribute_name for item in data
            )
        elif type == 1:
            new_entry = {
                "Secondary Attribute Name": attribute_name,
                "Description": description,
            }
            exists = any(
                item.get("Secondary Attribute Name") == attribute_name for item in data
            )
        if not exists:
            data.append(new_entry)
            with open(file_name, "w") as file:
                json.dump(data, file, indent=4)

    def process_article(self, result, secondary_attribute_path):
        secondary_attribute = result.get("Secondary Attribute Name")
        secondary_description = result.get("Description")
        if secondary_attribute and secondary_description:
            self.save_attributes(
                secondary_attribute, secondary_description, secondary_attribute_path, 1
            )

    def process_data(self, data_list):
        result = {}
        secondary_attributes = []
        seen_names = set() 

        for item in data_list:
            if item is None:
                continue
            primary_attr = item.get("Primary Attribute Name")
            secondary_attr_name = item.get("Secondary Attribute Name")
            description = item.get("Description")

            if secondary_attr_name in seen_names:
                continue

            seen_names.add(secondary_attr_name)

            secondary_attributes.append(
                {"Name": secondary_attr_name, "Description": description}
            )

            if "Primary Attribute" not in result:
                result["Primary Attribute"] = primary_attr

        result["Secondary Attributes"] = secondary_attributes

        return result

    def replace_secondary_attributes(self, data_list, attribute_dict):
        reverse_mapping = {
            attr: key for key, attrs in attribute_dict.items() for attr in attrs
        }

        for item in data_list:
            secondary_name = item.get("Secondary Attribute Name")
            if secondary_name in reverse_mapping:
                item["Secondary Attribute Name"] = reverse_mapping[secondary_name]

        return data_list

    def extract_and_convert(self, text):
        match = re.search(r"<Answer>\s*(\{.*?\})\s*</Answer>", text, re.DOTALL)
        if match:
            content = match.group(1)
            try:
                dictionary = ast.literal_eval(content)
                return dictionary
            except (SyntaxError, ValueError) as e:
                print(f"Error parsing content: {e}")
                return None
        else:
            print("No valid content found in <Answer> tags.")
            return None

    def data_convert(self, triplets):
        data = defaultdict(lambda: defaultdict(list))

        for triplet in triplets:
            category = triplet["Category"]
            feature = triplet["Feature"]
            method = triplet["Method"]
            data[category][feature].append(method)

        final_data = {"Category": [], "Feature": [], "Method": []}

        for category, features in data.items():
            final_data["Category"].append(category)
            feature_list = []
            method_list = []
            for feature, methods in features.items():
                feature_list.append(feature)
                method_list.append(methods)
            final_data["Feature"].append(feature_list)
            final_data["Method"].append(method_list)
        return final_data

    def extract_cite_name(self, text: str) -> List[str]:
        result = []
        cite_names = re.findall(r"\\cite\{(.*?)\}", text)
        for name in cite_names:
            if name not in result:
                result.append(name)
        return result

    def load_table_data(self, dir_path):
        data = []
        temp_data = []

        if not os.path.isdir(dir_path):
            print(f"The directory {dir_path} does not exist or is not accessible.")
            return None
        for filename in os.listdir(dir_path):
            if filename.endswith(".json"):
                file_path = os.path.join(dir_path, filename)
                with open(file_path, "r", encoding="utf-8") as file:
                    result = json.load(file) 
                    dict = {}
                    dict["Category"] = result["Primary Attribute Name"]
                    dict["Feature"] = result["Secondary Attribute Name"]
                    dict["Method"] = result["cite_name"]
                    dict["Order"] = result["order"] 
                    temp_data.append(dict)

        temp_data.sort(key=lambda x: x["Order"]) 

        for item in temp_data:
            data.append(
                {
                    "Category": item["Category"],
                    "Feature": item["Feature"],
                    "Method": item["Method"],
                }
            )

        return data

    def extract_section_content(self, tex_file_path: str, section_name: str) -> str:
        with open(tex_file_path, "r", encoding="utf-8") as file:
            content = file.read()
        pattern = re.compile(
            r"(\\section\{" + re.escape(section_name) + r"\}.*?)(?=\\section|$)",
            re.DOTALL,
        )
        match = pattern.search(content)
        if match:
            return match.group(1).strip()
        else:
            return None

    def extract_section_mainbody(self, tex_file_path: str, section_name: str) -> str:
        with open(tex_file_path, "r", encoding="utf-8") as file:
            content = file.read()

        pattern = re.compile(
            r"\\section\{"
            + re.escape(section_name)
            + r"\}(?:\s*\\label\{.*?\})?\s*(.*?)(?=\\subsection|\\section|$)",
            re.DOTALL,
        )

        match = pattern.search(content)
        if match:
            return match.group(1).strip()
        else:
            return None

    def extract_subsection_content(
        self, tex_file_path: str, subsection_name: str
    ) -> str:
        with open(tex_file_path, "r", encoding="utf-8") as file:
            content = file.read()
        pattern = re.compile(
            rf"(\\subsection\{{{re.escape(subsection_name)}\}}.*?)(?=(\\section|\\subsection|$))",
            re.DOTALL,
        )
        match = pattern.search(content)
        if match:
            return match.group(1).strip()
        else:
            return None

    def extract_subsections(self, text):
        subsection_pattern = r"(\\subsection\{.*?\}.*?)(?=\\subsection|$)"
        subsections = re.findall(subsection_pattern, text, re.DOTALL)

        title_pattern = r"\\subsection\{(.*?)\}"
        titles = [re.search(title_pattern, sub).group(1) for sub in subsections]
        return [sub.strip() for sub in subsections], [title for title in titles]

    def extract_section_title(self, text):
        section_pattern = r"\\section\{(.*?)\}"
        section_matches = re.findall(section_pattern, text, re.DOTALL)      
        if not section_matches:
            logger.warning("未找到任何section标题")
            return "Default Title"
        return section_matches[0] 

    def extract_subsection_title(self, text):
        section_pattern = r"\\subsection\{.*?\}"
        section_title = re.findall(section_pattern, text, re.DOTALL)
        if not section_title:
            logger.warning("未找到任何section标题")
            return "Default Title"
        return section_title[0]

    def supplement_data(self, current_data, dir_path, target_size):
        data_list = load_meta_data(dir_path)
        benchmark_list = []
        for data in data_list:
            if data["paper_type"] == "benchmark":
                benchmark_list.append(data)
        current_bib_names = {item["bib_name"] for item in current_data}

        remaining_data = []
        for item in benchmark_list:
            if (
                item["bib_name"] not in current_bib_names
                and item["attri"] is not None
                and len(item["attri"]["idea"]["benchmark abbreviation"].split()) < 2
                and self.convert_to_number(item["attri"]["dataset"]["size"]) is not None
                and self.convert_to_number(item["attri"]["dataset"]["size"]) < 10000000
            ):
                info = {
                    "name": item["attri"]["idea"]["benchmark abbreviation"],
                    "size": item["attri"]["dataset"]["size"],
                    "domain": item["attri"]["dataset"]["domain"],
                    "task format": item["attri"]["dataset"]["task format"],
                    "metric": item["attri"]["metrics"]["metric name"],
                    "bib_name": item["bib_name"],
                }
                remaining_data.append(info)
        supplemented_data = current_data[:]
        for item in remaining_data:
            if len(supplemented_data) < target_size:
                supplemented_data.append(item)
            else:
                break

        return supplemented_data

    def get_sections(self, survey_path: str) -> List[str]:
        tex = open(survey_path, "r").read()
        pattern = r"\\section{"
        match_l = list(re.finditer(pattern, tex))
        res = []
        for i in range(len(match_l) - 1):
            section_tex = tex[match_l[i].start() : match_l[i + 1].start()]
            res.append(section_tex)
        return res

    def save_table_file(self, latex_code, output_file):
        with open(output_file, "w") as file:
            file.write(latex_code)

    def generate_description(self, latex_code, content):
        prompt = load_prompt(
            f"{BASE_DIR}/resources/LLM/prompts/latex_table_builder/Table_description.txt",
            Latex=latex_code,
            Content=content,
        )
        result = self.chat_agent.remote_chat(
            text_content=prompt, model=ADVANCED_CHATAGENT_MODEL
        )
        result = self.extract_and_convert(result)
        if result is not None:
            caption = result.get("caption")
            introductory_sentence = result.get(
                "introductory sentence"
            )
            return caption, introductory_sentence
        return None, None

    def get_value_list(self, data):
        list1 = [list(d.values())[1] for d in data]
        list2 = [list(d.values())[2] for d in data]
        list3 = [list(d.values())[3] for d in data]
        return [list1, list2, list3]

    def validity_judge(self, data):
        count = 0
        for element in data:
            if "-" in element:
                count += 1

        if count > len(data) / 2:
            return 0
        return 1

    def format_string(self, s):
        if not s:  
            return s
        words = s.split(" ")
        formatted_words = []
        for word in words:
            if len(word) == 2: 
                formatted_word = word.upper()
            elif "-" in word: 
                parts = word.split("-")
                formatted_word = "-".join([parts[0].capitalize()] + parts[1:])
            else:
                formatted_word = word.capitalize()
            formatted_words.append(formatted_word)
        return " ".join(formatted_words)

    def calculate_similarity(self, list_of_strings, threshold=0.7):
        def preprocess(s):
            return " ".join(sorted(s.lower().split()))

        def similarity(s1, s2):
            return SequenceMatcher(None, s1, s2).ratio()

        def is_valid(s):
            return bool(s.strip()) and not re.fullmatch(r"[-_.]+", s.strip())

        filtered_strings = [s for s in list_of_strings if is_valid(s)]

        processed_strings = [preprocess(s) for s in filtered_strings]

        high_similarity_pairs = 0
        total_pairs = 0
        for s1, s2 in combinations(processed_strings, 2):
            total_pairs += 1
            if similarity(s1, s2) >= threshold:
                high_similarity_pairs += 1

        similarity_score = high_similarity_pairs / total_pairs if total_pairs > 0 else 0
        return similarity_score

    def convert_to_number(self, number_str):
        try:
            cleaned_str = number_str.replace(",", "")
            return int(cleaned_str)
        except ValueError:
            return None

    def parse_outline(self, data):
        section_titles = []
        subsection_titles = []

        for section in data["sections"]:
            if "section title" in section:
                section_titles.append(section["section title"])

            if "subsections" in section:
                for subsection in section["subsections"]:
                    if "subsection title" in subsection:
                        subsection_titles.append(subsection["subsection title"])
        return section_titles, subsection_titles

    def get_sections(self, survey_path: str) -> List[str]:
        tex = open(survey_path, "r").read()
        pattern = r"\\section{"
        match_l = list(re.finditer(pattern, tex))
        res = []
        for i in range(len(match_l) - 1):
            section_tex = tex[match_l[i].start() : match_l[i + 1].start()]
            res.append(section_tex)
        return res

    def get_title(self, section: str) -> str:
        title = re.findall(r"\\section\{([^}]+)\}", section)[0]
        return title