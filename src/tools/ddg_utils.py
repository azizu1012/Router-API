import re
import unicodedata
import dateparser
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from typing import Dict, List, Optional, Set, Tuple, Union

from src.tools.ddg_data import SearchDataMixin


class SearchUtilsMixin(SearchDataMixin):

    def _extract_date(self, text: str) -> Optional[datetime]:
        if not text:
            return None
        try:
            return dateparser.parse(text, languages=['vi', 'en'])
        except Exception as e:
            self.logger.warning(f"Error parsing date in _extract_date: {e}")
            return None

    def _extract_main_text(self, html_text: str) -> str:
        soup = BeautifulSoup(html_text, 'lxml')
        
        # Loại bỏ các thẻ không cần thiết
        for tag in soup(["script", "style", "noscript", "svg", "form", "button", "header", "footer", "nav", "aside"]):
            tag.decompose()
            
        # Tìm nội dung chính
        content = soup.find("article") or soup.find("main") or soup.body
        if not content:
            return ""
            
        text = content.get_text(separator=' ')
        text = re.sub(r"\b(cookie policy|accept cookies|subscribe|advertisement|all rights reserved)\b", " ", text, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", text).strip()
