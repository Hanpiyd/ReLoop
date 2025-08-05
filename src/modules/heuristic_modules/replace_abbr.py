import re
import logging

logger = logging.getLogger(__name__)

class AbbrReplacer(object):
    def __init__(self):
        self._abbr_dict = {}
        self.first_occurrences = set() 
        self.pattern = re.compile(r"\s+\(([A-Z]+)\)")
        self.punc_pattern = re.compile(r"[,.]\s*|\n")
        
    def find_abbr_pairs(self, content:str):
        segs = re.split(self.punc_pattern, content)
        for one in segs:
            one = one.strip()
            if one == "":
                continue
            matches = re.finditer(self.pattern, one)
            for match in matches:
                abbr = match.group(1)
                pos = match.start()
                if len(abbr.strip().split()) > 1:
                    continue
                words_num = len(abbr)
                words = one[:pos].split().split()[-words_num:]
                full_name = " ".join(words)
                if all(word[0].upper() == abbr_char for word, abbr_char in zip(words, abbr)):
                    if full_name not in self._abbr_dict:
                        self._abbr_dict[full_name] = abbr
                        self.first_occurrences.add(full_name)
                        
        return self._abbr_dict
    
    def replace_full_name_with_abbr(self, match):
        full_name_only = match.group(1)
        if full_name_only in self.first_occurrences:
            self.first_occurrences.remove(full_name_only)
            return match.group(0)
        return self._abbr_dict[full_name_only]
    
    def process(self, content:str):
        self.find_abbr_pairs(content=content)
        for full_name, abbr in self._abbr_dict.items():
            full_name_pattern = (
                r"\b(" + re.escape(full_name) + r")(\s+\(" + re.escape(abbr) + r"\))?"
            )
            content = re.sub(
                full_name_pattern, self.replace_full_name_with_abbr, content
            )
        return content