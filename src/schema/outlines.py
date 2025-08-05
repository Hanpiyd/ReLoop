import json
from pathlib import Path
import re
import logging
import os

from src.modules.utils import load_file_as_string, save_result

logger = logging.getLogger(__name__)

class SingleOutline:
    def __init__(self, title:str, desc:str, sub:list = []):
        self.title = title
        self.desc = desc
        self.sub = sub
        
    @staticmethod
    def construct_secondary_outline_from_dict(dic: dict) -> None:
        return SingleOutline(dic["subsection title"], dic["description"])
    
    @staticmethod
    def construct_primary_outline_from_dict(dic: dict) -> None:
        dic.setdefault("subsections", [])
        sub = [
            SingleOutline.construct_secondary_outline_from_dict(x)
            for x in dic["subsections"]
        ]
        return SingleOutline(dic["section title"], dic["description"], sub)
            
    def __str__(self):
        return "\n".join([self.title, self.desc])
    
class Outlines:
    def __init__(self, title:str, sections: list[SingleOutline]):
        self.title = title
        self.sections = sections
        
    @staticmethod
    def from_saved(file_path: str) -> "Outlines":
        dic = json.loads(load_file_as_string(file_path))
        title = dic["title"]
        sections = []
        for sec in dic["sections"]:
            sections.append(SingleOutline.construct_primary_outline_from_dict(sec))
        logger.debug("construct outlines from saved path: {}".format(file_path))
        return Outlines(title, sections)
    
    @staticmethod
    def from_dict(dic: dict):
        title = dic["title"]
        sections = []
        for sec in dic["sections"]:
            sections.append(SingleOutline.construct_primary_outline_from_dict(sec))
        return Outlines(title, sections)
    
    def save_to_file(self, file_path:Path):
        dic = self.to_dict()
        save_result(json.dumps(dic, indent=4), file_path)
        logger.debug(f"Outlines saved to {file_path}")
        
        
    def to_dict(self):
        dic = {"title" : self.title, "sections" : []}
        for section in self.sections:
            dic["sections"].append(
                {
                    "section title": section.title,
                    "description": section.desc,
                    "subsections": [
                        {
                            "subsection title": subsection.title,
                            "description": subsection.desc,
                        }
                        for subsection in section.sub
                    ]
                }
            )
        return dic
    

    def __str__(self):
        res = [self.title]
        for i, sec in enumerate(self.sections):
            res.append(f"{i + 1}. " + sec.__str__())
            for j, subsec in enumerate(sec.sub):
                res.append(f"{i + 1}.{j + 1} " + subsec.__str__())
            
        return "\n".join(res)
    
    def serial_no_to_single_outline(self, serial_no_raw):
        try:
            if "." in serial_no_raw:
                serial_no = re.search(r"\d+\.\d*", serial_no_raw).group(0)
                primary_section_index = int(serial_no.split(".")[0])
                secondary_section_index = serial_no.split(".")[1]
                if secondary_section_index != "":
                    secondary_section_index = int(secondary_section_index)
                    return self.sections[primary_section_index - 1].sub[secondary_section_index - 1]
                else:
                    return self.sections[primary_section_index - 1]
            else:
                serial_no = re.search(r"\d+", serial_no_raw).group(0)
                primary_section_index = int(serial_no)
                return self.sections[primary_section_index - 1]
        except Exception as e:
            logger.error(
                f"Error occurs: {e}, the serial_no_raw is {serial_no_raw}, the serial_no is {serial_no}"
            )
            
    