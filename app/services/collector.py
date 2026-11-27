"""站点批量采集引擎：列表页发现 → 翻页 → 详情解析 → 去重入库。

与 `crawler.py`（单篇链接采集）的分工：
    crawler.fetch_notice   —— 用户粘贴一个链接，抓一篇；
    collector.crawl_source —— 按 `config/sources.json` 抓一个栏目的一批通知。
两者共用本文件的请求层与详情解析器，避免出现"两条采集链路解析结果不一致"。

设计取舍（都是踩过坑之后定下来的）：
1. **正文容器按选择器顺序命中，绝不按文本长度挑最长**。CMS 外层 div 往往把
   面包屑、"发布时间"、浏览量一起包进来，取最长会把这些噪声写进正文，
   而浏览量每次访问都在变，会让内容指纹抖动、同一条通知被反复判为"有更新"。
2. **附件必须在正文容器之外单独扫**。很多站点的附件区与正文是兄弟节点，
   只扫正文会一条附件都抓不到。
3. **翻页只认页面上真实存在的链接**，不猜 URL 规律；且对"查看更多"这类
   同时用于跨栏目跳转的文案，要求跳转后与当前页同目录才算翻页，
   否则会一边翻页一边漂到别的栏目去，且全程都是 200，不会报错。
4. **图片型通知**（讲座海报）正文里没有文字，如实记 content_type=image，
   并把图片地址作为附件留下，否则这条通知入库后等于空记录。
"""
from __future__ import annotations

import ipaddress
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, NavigableString

from app.config import BASE_DIR, settings
from app.services.cleaner import clean_text
from app.services.textutil import normalize_text

SOURCES_FILE = BASE_DIR / "config" / "sources.json"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

DATE_RE = re.compile(r"(20\d{2})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})")
SHORT_DATE_RE = re.compile(r"(?<![\d年])(\d{1,2})\s*[-/月]\s*(\d{1,2})(?![\d月])")

STRIP_TAGS = ["script", "style", "nav", "header", "footer", "aside", "form", "iframe", "noscript"]

# 详情页正文容器，**按优先级**排列（前面的更精确）。
CONTENT_SELECTORS = [
    "#art_content",          # 武汉理工研究生院 .art_text 外层
    ".art_text",
    "#bfArticleContent",     # 综合信息网
    ".article_content",
    "#vsb_content",          # 博达 / VSB 系 CMS
    ".v_news_content",
    ".wp_articlecontent",    # 正方系
    ".TRS_Editor",           # TRS 编辑器输出
    ".view",                 # 新闻经纬（.content 是外层，必须排它前面）
    ".news_content",
    ".article-content",
    ".content-detail",
    ".article",
    "#content",
    ".main-content",
    ".content",
]

TITLE_SELECTORS = [".article_title", "h1.article_title", ".content-detail h2", "h1", "h2"]

# 明确的正文容器：命中即采用，哪怕里面没有文字。
# 图片型通知（正文只有一张海报）如果不这样处理，会继续往下命中外层容器，
# 把"首页 > 综合新闻 + 标题 + 发布时间"当成正文写进库。
PRIMARY_SELECTORS = ("#art_content", ".art_text", "#bfArticleContent", ".article_content", ".view")

ATTACHMENT_EXT = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".txt", ".csv",
}

# 列表页里这些文案的链接不是通知正文
NAV_WORDS = {
    "更多", "查看更多", "首页", "上一页", "下一页", "下页", "尾页", "末页",
    "返回", "顶部", "登录", "注册", "English", "网站地图", "联系我们",
}

NEXT_TEXTS = {"下一页", "下页", "后一页", "后页", "next", "More", "更多", "查看更多"}
SAME_DIR_ONLY = {"更多", "查看更多", "More"}

# 只删明确无意义的跟踪参数。`from` 这类在校园 CMS 里常用来切栏目，
# 删掉就会把两个栏目归一成同一个 URL，第二个栏目全部被判重复且没有任何报错。
TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                   "spm", "share_token", "share_source"}

NOISE_LINE_RE = [
    re.compile(r"^\s*浏览量\s*[:：]?\s*\d*\s*$"),
    re.compile(r"^\s*(点赞|打印本页|关闭窗口|返回顶部|分享到|责任编辑)\s*[:：]?.*$"),
    re.compile(r"^\s*(上一篇|下一篇)\s*[:：].*$"),
    re.compile(r"^\s*当前位置\s*[:：]?\s*$"),
    re.compile(r"^\s*字号\s*[:：]?\s*$"),
]

# 被排版拆散的碎片行：CMS 常把数字包在单独的 span 里，
# get_text("\n") 之后「2025」「年11月20日」会变成两行，「2」「人」也会。
# 直接按行清洗会把纯数字行当噪声删掉，结果是
# 「报名截止时间 2025年月日20:00」—— 截止日期整条消失，且完全无声。
FRAGMENT_RE = re.compile(r"^[0-9A-Za-z\-—~～/.%:：年月日]{1,6}$")

BLOCK_TAGS = ("p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5",
              "table", "section", "article", "blockquote", "dd", "dt")


class CrawlError(RuntimeError):
    """采集失败：调用方据此降级到手动录入，不应中断整轮任务。"""


@dataclass
class SourceCfg:
    id: str
    name: str
    url: str
    category: str = "其他"
    enabled: bool = True
    list_selector: str = ""
    detail: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict) -> "SourceCfg":
        return cls(
            id=str(raw.get("id") or "").strip(),
            name=str(raw.get("name") or "").strip() or str(raw.get("id") or ""),
            url=str(raw.get("url") or "").strip(),
            category=str(raw.get("category") or "其他"),
            enabled=bool(raw.get("enabled", True)),
            list_selector=str(raw.get("list_selector") or "").strip(),
            detail=raw.get("detail") if isinstance(raw.get("detail"), dict) else {},
        )

    def selectors(self, key: str) -> list[str]:
        """detail 段支持 "a, b" 或 ["a","b"] 两种写法，统一成列表。"""
        raw = self.detail.get(key)
        if not raw:
            return []
        if isinstance(raw, str):
            return [s.strip() for s in raw.split(",") if s.strip()]
        if isinstance(raw, (list, tuple)):
            return [str(s).strip() for s in raw if str(s).strip()]
        return []

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "url": self.url,
            "category": self.category, "enabled": self.enabled,
            "list_selector": self.list_selector,
        }


# ---------------------------------------------------------------- 配置

def load_config() -> dict:
    if not SOURCES_FILE.exists():
        return {"settings": {}, "sources": []}
    try:
        return json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CrawlError(f"采集源配置解析失败：{exc}") from exc


def load_sources(only_enabled: bool = True) -> list[SourceCfg]:
    conf = load_config()
    out = [SourceCfg.from_dict(raw) for raw in conf.get("sources", [])]
    return [s for s in out if s.url and (s.enabled or not only_enabled)]


def get_source(source_id: str) -> SourceCfg:
    for src in load_sources(only_enabled=False):
        if src.id == source_id:
            return src
    raise CrawlError(f"未找到采集源：{source_id}")


def _setting(key: str, default):
    return load_config().get("settings", {}).get(key, default)


# ---------------------------------------------------------------- 请求层

def check_url(url: str) -> str:
    """协议补全 + 内网拦截 + 白名单校验。白名单是合规底线，不可由前端绕过。"""
    url = (url or "").strip()
    if not url:
        raise CrawlError("链接不能为空")
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if not host:
        raise CrawlError("链接格式不正确")

    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
            raise CrawlError("不允许采集内网地址")
    except ValueError:
        pass
    if host == "localhost" or host.endswith(".local"):
        raise CrawlError("不允许采集本地地址")

    allowed = settings.CRAWL_ALLOWED_DOMAINS
    if allowed and not any(host == d or host.endswith("." + d) for d in allowed):
        raise CrawlError(f"域名 {host} 不在采集白名单内（白名单：{', '.join(allowed)}）")
    return url


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": _setting("user_agent", DEFAULT_UA),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        "Connection": "keep-alive",
    })
    return session


def fetch_page(url: str, session: requests.Session | None = None,
               retry: int | None = None) -> tuple[str, str]:
    """返回 (html, 最终 URL)。失败抛 CrawlError，由上层记录并继续下一个。"""
    url = check_url(url)
    session = session or build_session()
    retry = _setting("retry", 2) if retry is None else retry
    timeout = _setting("timeout", settings.CRAWL_TIMEOUT)

    last_error = ""
    for attempt in range(retry + 1):
        try:
            resp = session.get(url, timeout=timeout, stream=True, allow_redirects=True)
            if resp.status_code != 200:
                last_error = f"目标站点返回 {resp.status_code}"
                # 4xx 重试没有意义，直接放弃
                if 400 <= resp.status_code < 500:
                    break
                raise CrawlError(last_error)
            raw = resp.raw.read(settings.CRAWL_MAX_BYTES + 1, decode_content=True)
            if len(raw) > settings.CRAWL_MAX_BYTES:
                raise CrawlError("页面体积超过限制")
            encoding = resp.encoding
            if not encoding or encoding.lower() in ("iso-8859-1", "ascii"):
                # 校园站点大量使用 GBK 且响应头不声明，靠内容探测更可靠
                encoding = resp.apparent_encoding or "utf-8"
            html = raw.decode(encoding, errors="ignore")
            return html, resp.url
        except CrawlError:
            raise
        except requests.RequestException as exc:
            last_error = f"网络请求失败：{type(exc).__name__}"
            if attempt < retry:
                time.sleep(1.0 + attempt)
    raise CrawlError(last_error or "采集失败")


# ---------------------------------------------------------------- 通用工具

def strip_noise_nodes(soup: BeautifulSoup) -> BeautifulSoup:
    for tag in soup(STRIP_TAGS):
        tag.decompose()
    return soup


def normalize_url(url: str) -> str:
    """去锚点、去跟踪参数；其余 query 参数保留（可能与正文内容有关）。"""
    url = (url or "").strip().split("#", 1)[0]
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    if parts.query:
        kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if k.lower() not in TRACKING_PARAMS]
        parts = parts._replace(query=urlencode(kept, doseq=True) if kept else "")
    return urlunsplit(parts)


def normalize_date(text: str, ref: date | None = None) -> str:
    """统一成 YYYY-MM-DD。交给 date() 校验真实天数，避免 2-30 这种假日期入库。"""
    if not text:
        return ""
    m = DATE_RE.search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return ""
    m = SHORT_DATE_RE.search(text)
    if m:
        ref = ref or date.today()
        try:
            guess = date(ref.year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            return ""
        # 列表页不写年份时，出现在"未来"明显过多说明是去年的通知
        if guess > ref + timedelta(days=60):
            try:
                guess = guess.replace(year=guess.year - 1)
            except ValueError:
                return ""
        return guess.isoformat()
    return ""


def clean_title(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").replace("\u200b", "")).strip()
    text = re.sub(r"^[【\[]\s*(?:置顶|推荐|热门|新)\s*[】\]]", "", text)
    # 列表页常见"标题 2025-01-21"或"标题（2025-01-21）"
    text = DATE_RE.sub("", text)
    text = re.sub(r"[（(]\s*[)）]\s*$", "", text)
    return text.strip(" ·-—_|")


def _strip_site_suffix(title: str) -> str:
    for sep in (" - ", "_", "—", "－", "|"):
        if sep in title:
            head, tail = title.rsplit(sep, 1)
            if len(head) >= 6 and ("大学" in tail or "学院" in tail
                                   or "新闻网" in tail or "欢迎访问" in tail):
                return head.strip()
    if title.startswith("欢迎访问") and "网" in title:
        return ""
    return title.strip()


def _looks_like_article(url: str, base_url: str) -> bool:
    """粗筛详情链接：排除栏目根、锚点、javascript、明显不是文章的地址。"""
    low = (url or "").lower()
    if not low.startswith(("http://", "https://")):
        return False
    if any(low.split("?")[0].endswith(ext) for ext in ATTACHMENT_EXT):
        return False
    base_path = urlsplit(base_url).path.rstrip("/")
    path = urlsplit(url).path
    if path.rstrip("/") == base_path:
        return False
    return True


# ---------------------------------------------------------------- 列表页

def _guess_list_container(soup: BeautifulSoup):
    """通用列表容器探测：按"链接数 + 日期数 - 导航词数"给候选打分。

    校园 CMS 的列表几乎都是 ul>li 或 table>tr，但 class 名千差万别
    （list_t / art-list / newslist / list-box…），与其穷举，不如按结构特征打分。
    """
    best, best_score = None, 0.0
    for block in soup.find_all(["ul", "ol", "table", "tbody", "div"]):
        rows = block.find_all(["li", "tr"], recursive=False)
        if len(rows) < 3:
            if block.name != "div":
                continue
            rows = [d for d in block.find_all("div", recursive=False) if d.find("a")]
            if len(rows) < 5:
                continue
        links = dates = nav = 0
        for row in rows:
            a = row.find("a", href=True)
            if not a:
                continue
            links += 1
            text = row.get_text(" ", strip=True)
            if DATE_RE.search(text) or SHORT_DATE_RE.search(text):
                dates += 1
            if clean_title(a.get_text(" ", strip=True)) in NAV_WORDS:
                nav += 1
        if links < 3:
            continue
        score = links + dates * 3 - nav * 2
        if score > best_score:
            best, best_score = block, score
    return best


def parse_list(html: str, page_url: str, cfg: SourceCfg | None = None) -> list[dict]:
    """解析列表页，返回 [{title, url, publish_date, unit}]。"""
    soup = strip_noise_nodes(BeautifulSoup(html, "lxml"))

    rows = []
    if cfg and cfg.list_selector:
        try:
            rows = soup.select(cfg.list_selector)
        except Exception:                       # 配置写错不能让整轮采集失败
            rows = []
    if not rows:
        container = _guess_list_container(soup)
        rows = container.find_all(["li", "tr"], recursive=False) if container else []

    items: list[dict] = []
    seen: set[str] = set()
    today = date.today()

    for row in rows:
        anchors = [a for a in row.find_all("a", href=True)]
        if not anchors:
            continue
        # 一行里常有"标题 + 图片 + 更多"多个链接，取文本最长的那个当标题
        anchors.sort(key=lambda a: len(a.get_text(" ", strip=True)), reverse=True)
        link = anchors[0]

        href = urljoin(page_url, link.get("href").strip())
        if not _looks_like_article(href, page_url):
            continue

        title = clean_title(link.get("title") or link.get_text(" ", strip=True))
        if len(title) < 6 or title in NAV_WORDS:
            continue

        url = normalize_url(href)
        if url in seen:
            continue
        seen.add(url)

        row_text = row.get_text(" ", strip=True)
        publish = normalize_date(row_text, today)

        # 单位：优先取不含日期的元信息节点（不同站点分别用 .date / .source / .list_tag）
        unit = ""
        for sel in (".list_tag", ".date", ".source", ".unit", ".keyword"):
            node = row.select_one(sel)
            if node:
                text = node.get_text(" ", strip=True).strip("【】 ")
                if text and not DATE_RE.search(text) and not SHORT_DATE_RE.search(text):
                    unit = text
                    break

        items.append({"title": title, "url": url, "publish_date": publish, "unit": unit})

    return items


def parse_pagination(html: str, page_url: str) -> dict:
    """返回 {next_url, total_pages, current_page}。只认页面里真实存在的链接。"""
    soup = BeautifulSoup(html, "lxml")
    info: dict = {"next_url": "", "total_pages": 0, "current_page": 0}

    m = re.search(r"共\s*(\d+)\s*页", soup.get_text(" ", strip=True))
    if m:
        info["total_pages"] = int(m.group(1))

    for sel in (".pages a.current", ".pagination .current", ".page a.current",
                "a.current", "li.active a"):
        node = soup.select_one(sel)
        if node and node.get_text(strip=True).isdigit():
            info["current_page"] = int(node.get_text(strip=True))
            break

    cur_path = urlsplit(page_url).path
    cur_dir = cur_path.rsplit("/", 1)[0]

    for a in soup.find_all("a"):
        text = a.get_text(" ", strip=True)
        if text not in NEXT_TEXTS:
            continue
        href = (a.get("href") or "").strip()
        if not href or href.lower().startswith(("javascript", "#", "mailto")):
            continue
        full = urljoin(page_url, href)
        if text in SAME_DIR_ONLY and urlsplit(full).path.rsplit("/", 1)[0] != cur_dir:
            continue                          # 跨栏目的"查看更多"，不是翻页
        info["next_url"] = normalize_url(full)
        break

    if not info["next_url"] and info["current_page"]:
        want = str(info["current_page"] + 1)
        for a in soup.find_all("a", href=True):
            if a.get_text(strip=True) != want:
                continue
            href = a["href"].strip()
            if href.lower().startswith(("javascript", "#")):
                continue
            full = urljoin(page_url, href)
            if urlsplit(full).path.rsplit("/", 1)[0] != cur_dir:
                continue
            info["next_url"] = normalize_url(full)
            break

    # 防自环：下一页等于当前页时视为到底
    if info["next_url"] and info["next_url"].rstrip("/") == normalize_url(page_url).rstrip("/"):
        info["next_url"] = ""
    return info


# ---------------------------------------------------------------- 详情页

def _pick_content(soup: BeautifulSoup, cfg: SourceCfg | None):
    """按优先级命中正文容器。

    多命中时用"包含关系 + 体量比"再做一次收敛：
    若外层容器只是比内层多出 30% 以内的文字，说明多出来的是面包屑/发布时间，
    取内层更干净。这样既能吃到站点模板的精确容器，也不会把页面骨架写进正文。
    """
    ordered = list(cfg.selectors("content_selector")) if cfg else []
    ordered += [s for s in CONTENT_SELECTORS if s not in ordered]

    hits = []
    for selector in ordered:
        try:
            node = soup.select_one(selector)
        except Exception:
            continue
        if node is None or any(node is h for h in hits):
            continue
        hits.append((selector, node))

    def text_len(node) -> int:
        return len(node.get_text(" ", strip=True))

    kept = []
    for selector, node in hits:
        wrapped = any(
            node is not other and node in other.parents and text_len(other) < text_len(node) * 1.3
            for _, other in hits
        )
        if wrapped:
            continue                       # 它只是更精确容器的外层包装
        kept.append((selector, node))

    for selector, node in kept:
        if text_len(node) >= 40:
            return node
        if selector in PRIMARY_SELECTORS:
            return node                    # 主容器命中即采用，宁可空也不要噪声
    return kept[0][1] if kept else None


def _extract_title(soup: BeautifulSoup, cfg: SourceCfg | None) -> str:
    for selector in (cfg.selectors("title_selector") if cfg else []) + TITLE_SELECTORS:
        try:
            node = soup.select_one(selector)
        except Exception:
            continue
        if not node:
            continue
        text = clean_title(node.get_text(" ", strip=True))
        if len(text) >= 6:
            return _strip_site_suffix(text)

    og = soup.select_one('meta[property="og:title"]')
    if og and og.get("content"):
        text = clean_title(og["content"])
        if text:
            return _strip_site_suffix(text)

    if soup.title:
        return _strip_site_suffix(clean_title(soup.title.get_text(" ", strip=True)))
    return ""


def _extract_publish(soup: BeautifulSoup, cfg: SourceCfg | None) -> str:
    """发布时间：配置选择器 → 「发布/发布时间」标签后的日期 → 页面首个日期。

    必须"在标签处切开只取标签之后的日期"。直接对整段跑正则取第一个日期，
    会把标题里的「关于2026年10月20日举办…」当成发布时间，进而污染年份推断。
    """
    for selector in (cfg.selectors("date_selector") if cfg else []):
        try:
            for node in soup.select(selector):
                value = normalize_date(node.get_text(" ", strip=True))
                if value:
                    return value
        except Exception:
            continue

    label_re = re.compile(r"(?:发布时间|发布日期|发文日期|发布于|发布|posted|published)\s*[:：]?\s*",
                          re.IGNORECASE)
    for node in soup.find_all(["div", "span", "p", "li", "td"]):
        text = node.get_text(" ", strip=True)
        if not text or len(text) > 80 or "浏览量" in text:
            continue
        m = label_re.search(text)
        if not m:
            continue
        value = normalize_date(text[m.end():m.end() + 24])
        if value:
            return value

    for node in soup.find_all(["div", "span", "p", "li", "td"]):
        text = node.get_text(" ", strip=True)
        if 0 < len(text) <= 40 and "浏览" not in text:
            value = normalize_date(text)
            if value:
                return value
    return ""


def _extract_attachments(soup: BeautifulSoup, page_url: str,
                         content_node, cfg: SourceCfg | None) -> list[dict]:
    """附件既可能在正文里，也可能在正文外的附件区（甚至只在页面底部），三处都扫。"""
    scopes = []
    for selector in (cfg.selectors("attachment_selector") if cfg else []):
        try:
            scopes.extend(soup.select(selector))
        except Exception:
            continue
    for selector in (".article_appendix", ".appendix", ".fj", ".attachment", "#fj"):
        try:
            scopes.extend(soup.select(selector))
        except Exception:
            scopes.append(None)
    if content_node is not None:
        scopes.append(content_node)
    scopes.append(soup)                      # 兜底：整页扫，附带链接多半就是附件

    found, seen = [], set()
    for scope in scopes:
        if scope is None:
            continue
        for a in scope.find_all("a", href=True):
            href = urljoin(page_url, a["href"].strip())
            path = urlsplit(href).path.lower()
            ext = "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
            if ext not in ATTACHMENT_EXT:
                continue
            if href in seen:
                continue
            seen.add(href)
            found.append({
                "name": clean_title(a.get_text(" ", strip=True)) or path.rsplit("/", 1)[-1],
                "url": href,
            })
    return found[:10]


# 单元格分隔符用 U+001F（Unit Separator）而不是 \t：
# textutil.normalize_text 里的 _SPACE_RE = [ \t\u3000]+ 会把制表符压成空格，
# 表格列边界还没走到解析器就被抹平了。\x1f 是 ASCII 控制符，正文里不会自然出现，
# 且不在任何空白类里，能完整穿过清洗链路。
TABLE_CELL_SEP = "\x1f"


def _table_to_text(table) -> str:
    """把 <table> 序列化成「行用换行、单元格用制表符」的规整文本。

    这一步是整个表格链路的地基。原先 `get_text("")` 会让行内所有 <td>
    直接首尾相接（`085600材料与化工1027-87164677`），列边界一旦丢掉，
    下游再怎么改正则也分不出「哪一段是专业代码、哪一段是电话」——
    一维字符串里没有「列」这个概念，信息是不可逆丢失的。

    保留列边界后，`services/tables.py` 才能给每列打语义标签并映射到
    业务字段。行内仍有换行残留时压成空格，避免破坏「一行一记录」的假设。
    """
    rows: list[str] = []
    for tr in table.find_all("tr"):
        # 只取本行直属单元格，避免嵌套表格的格子被重复计入
        cells = []
        for cell in tr.find_all(["td", "th"]):
            if cell.find_parent("tr") is not tr:
                continue
            text = cell.get_text(" ", strip=True)
            text = re.sub(r"\s+", " ", text).strip()
            cells.append(text)
        if cells and any(cells):
            rows.append(TABLE_CELL_SEP.join(cells))
    return "\n" + "\n".join(rows) + "\n"


def _node_text(node) -> str:
    """按"块级换行、行内拼接"取文本。

    直接用 `get_text("\\n")` 会在每个 inline 元素边界插换行，
    而校园 CMS 习惯把数字、单位、日期包进各自的 <span>，
    结果一句「成员不超过 2 人」被拆成「不超过 / 2 / 人」三行，
    句读切分、条件抽取、日期定位全部失效。这里改为：
    <br> 与块级元素才换行，行内元素直接拼接（中文场景不需要补空格）。

    表格是唯一例外：它必须**先**按单元格序列化（见 `_table_to_text`），
    不能交给下面统一的块级换行处理。`tr` 与 `table` 都在 BLOCK_TAGS 里，
    若先走通用逻辑，行内格子会被拼成一条无分隔的长串，列语义当场丢失，
    之后无论怎么修正则都补不回来。所以这里倒序处理（先内层后外层），
    把每张表整体换成一个规整文本节点，再走通用流程。
    """
    for table in reversed(node.find_all("table")):
        if table.find_parent("table") is not None:
            continue  # 内层表已随外层一起序列化
        table.replace_with(NavigableString(_table_to_text(table)))
    for br in node.find_all("br"):
        br.replace_with(NavigableString("\n"))
    for tag in node.find_all(BLOCK_TAGS):
        tag.insert_before(NavigableString("\n"))
        tag.insert_after(NavigableString("\n"))
    return node.get_text("")


def _merge_fragments(lines: list[str]) -> list[str]:
    """把被排版拆散的数字/字母碎片粘回相邻行。

    「2025」「年11月20日」→「2025年11月20日」；「2」「人」→「2人」。
    宁可粘错也不能丢：丢掉一个数字，截止日期就变成「2025年月日」，
    下游的年份推断和到期提醒全部失真。

    表格行不参与：一个短段落（如「1」「x」）正好符合碎片特征，若被粘到
    下一行表头前面，那一行的列数就变了，整张表的列语义全部错位。
    所以遇到表格行时先把缓冲单独落地，再原样追加该行。
    """
    out: list[str] = []
    buf = ""
    for line in lines:
        if TABLE_CELL_SEP in line:
            if buf:
                out.append(buf)
                buf = ""
            out.append(line)
            continue
        if FRAGMENT_RE.match(line) and len(line) <= 6:
            buf += line
            continue
        if buf:
            line = buf + line
            buf = ""
        out.append(line)
    if buf:
        if out:
            out[-1] += buf
        else:
            out.append(buf)
    return out


def parse_detail(html: str, page_url: str, cfg: SourceCfg | None = None) -> dict:
    soup = strip_noise_nodes(BeautifulSoup(html, "lxml"))

    title = _extract_title(soup, cfg)
    publish = _extract_publish(soup, cfg)
    content_node = _pick_content(soup, cfg)

    raw = _node_text(content_node) if content_node else ""
    lines = []
    for line in raw.split("\n"):
        line = line.strip()
        if not line or any(rx.match(line) for rx in NOISE_LINE_RE):
            continue
        lines.append(line)
    lines = _merge_fragments(lines)
    # 正文首行常重复标题，去掉以免入库后标题翻倍
    if lines and title and (lines[0] in title or title in lines[0]) and len(lines[0]) <= len(title) + 8:
        lines = lines[1:]
    content = clean_text("\n".join(lines))

    has_image = content_node is not None and content_node.find("img") is not None
    if content:
        content_type = "text"
    elif has_image:
        content_type = "image"
        alts = [a.get("alt") or a.get("title") or "" for a in content_node.find_all("img")]
        content = "\n".join(x.strip() for x in alts if x and x.strip())
    else:
        content_type = "empty"

    attachments = _extract_attachments(soup, page_url, content_node, cfg)
    if content_type == "image" and not attachments and content_node is not None:
        for img in content_node.find_all("img")[:3]:
            src = img.get("src") or img.get("data-src") or ""
            if src and not src.startswith("data:"):
                attachments.append({"name": "正文图片", "url": urljoin(page_url, src)})

    return {
        "title": title,
        "content": content,
        "content_type": content_type,
        "publish_time": publish,
        "source_url": normalize_url(page_url),
        "attachments": attachments,
        "chars": len(content),
    }


# ---------------------------------------------------------------- 采集编排

def crawl_source(cfg: SourceCfg, limit: int = 10, max_pages: int = 1,
                 delay: float | None = None, fetch_detail: bool = True,
                 stop_after_known: int = 0, known_titles: set[str] | None = None,
                 known_urls: set[str] | None = None,
                 session: requests.Session | None = None) -> dict:
    """抓取单个栏目。

    stop_after_known > 0 时启用增量模式：连续 N 条都属于已入库内容即停止，
    避免每次全量重抓、也避免对站点造成不必要的请求压力。

    known_titles / known_urls 为已入库集合：命中即在抓取阶段跳过（不会占用
    每源条数配额，也不再请求详情页），让翻页继续去找真正的新内容。
    """
    delay = _setting("delay", 0.8) if delay is None else delay
    session = session or build_session()
    # 统一归一化，避免全角/零宽/空白差异导致已收录标题匹配不上
    known_titles = {normalize_text(t) for t in known_titles} if known_titles else set()
    known_urls = {normalize_text(u) for u in known_urls} if known_urls else set()

    started = time.time()
    stats = {"pages": 0, "candidates": 0, "detail_ok": 0, "detail_fail": 0, "skipped_known": 0}
    errors: list[str] = []
    items: list[dict] = []
    visited_pages: set[str] = set()
    page_url = normalize_url(cfg.url)

    while page_url and stats["pages"] < max_pages:
        if page_url in visited_pages:
            break
        visited_pages.add(page_url)
        try:
            html, final_url = fetch_page(page_url, session=session)
        except CrawlError as exc:
            errors.append(f"列表页 {page_url}：{exc}")
            break
        stats["pages"] += 1

        rows = parse_list(html, final_url, cfg)
        stats["candidates"] += len(rows)

        consecutive_known = 0
        for row in rows:
            if len(items) >= limit:
                break
            row_url = row.get("url", "")
            row_title = normalize_text(row["title"])
            if row_title in known_titles or (row_url and row_url in known_urls):
                stats["skipped_known"] += 1
                consecutive_known += 1
                if stop_after_known and consecutive_known >= stop_after_known:
                    break
                continue
            consecutive_known = 0

            if not fetch_detail:
                items.append({**row, "content": "", "content_type": "link", "attachments": []})
                continue
            try:
                detail_html, detail_url = fetch_page(row["url"], session=session)
                detail = parse_detail(detail_html, detail_url, cfg)
            except CrawlError as exc:
                stats["detail_fail"] += 1
                errors.append(f"详情页 {row['url']}：{exc}")
                # 详情抓不到时保留列表页信息，宁可少字段也不要整条丢掉
                items.append({**row, "content": "", "content_type": "link",
                              "attachments": [], "error": str(exc)})
                continue

            stats["detail_ok"] += 1
            items.append({
                "title": detail["title"] or row["title"],
                "url": row["url"],
                "publish_date": detail["publish_time"] or row["publish_date"],
                "unit": row["unit"],
                "content": detail["content"],
                "content_type": detail["content_type"],
                "attachments": detail["attachments"],
                "chars": detail["chars"],
            })
            time.sleep(delay)

        if len(items) >= limit or (stop_after_known and consecutive_known >= stop_after_known):
            break
        page_info = parse_pagination(html, final_url)
        page_url = page_info["next_url"]
        if page_url:
            time.sleep(delay)

    avg_chars = round(sum(i.get("chars", 0) for i in items) / len(items), 1) if items else 0
    return {
        "source": cfg.to_dict(),
        "items": items,
        "stats": {**stats, "items": len(items), "avg_chars": avg_chars,
                  "elapsed": round(time.time() - started, 1)},
        "errors": errors[:10],
    }


def crawl_all(source_ids: list[str] | None = None, limit: int | None = None,
              max_pages: int | None = None, fetch_detail: bool = True,
              known_titles: set[str] | None = None,
              known_urls: set[str] | None = None,
              stop_after_known: int = 0) -> dict:
    """遍历全部启用的源。单个源失败不影响其他源，失败原因随结果一起返回。"""
    limit = _setting("max_items", 10) if limit is None else limit
    max_pages = _setting("max_pages", 1) if max_pages is None else max_pages
    sources = load_sources()
    if source_ids:
        wanted = set(source_ids)
        sources = [s for s in sources if s.id in wanted]

    session = build_session()
    results = []
    for cfg in sources:
        try:
            results.append(crawl_source(cfg, limit=limit, max_pages=max_pages,
                                        fetch_detail=fetch_detail,
                                        known_titles=known_titles,
                                        known_urls=known_urls,
                                        stop_after_known=stop_after_known,
                                        session=session))
        except Exception as exc:                       # 单源异常兜底
            results.append({"source": cfg.to_dict(), "items": [],
                            "stats": {"items": 0}, "errors": [f"采集异常：{str(exc)[:120]}"]})

    total = sum(r["stats"].get("items", 0) for r in results)
    return {
        "total": total,
        "sources": results,
        "summary": {
            "sources": len(results),
            "ok": sum(1 for r in results if r["errors"] == []),
            "failed": [r["source"]["id"] for r in results if r["errors"]],
            "elapsed": round(sum(r["stats"].get("elapsed", 0) for r in results), 1),
        },
    }


def save_snapshot(result: dict, path: Path) -> Path:
    """把采集结果落成 JSON 快照，用于离线复现与申报材料举证。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": result.get("total", 0),
        "summary": result.get("summary", {}),
        "sources": result.get("sources", []),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
