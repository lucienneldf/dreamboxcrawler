import streamlit as st

# ============ 表格列表管理 ============
TABLE_LIST_PATH = "data/tables.json"

def load_table_list():
    """加载表格列表"""
    if os.path.exists(TABLE_LIST_PATH):
        with open(TABLE_LIST_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_table_list(table_list):
    """保存表格列表"""
    os.makedirs(os.path.dirname(TABLE_LIST_PATH), exist_ok=True)
    with open(TABLE_LIST_PATH, 'w', encoding='utf-8') as f:
        json.dump(table_list, f, ensure_ascii=False, indent=2)

def add_table_to_list(name, app_token, table_id):
    """添加表格到列表"""
    table_list = load_table_list()
    for t in table_list:
        if t['app_token'] == app_token:
            return False
    table_list.append({
        'name': name,
        'app_token': app_token,
        'table_id': table_id,
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M')
    })
    save_table_list(table_list)
    return True

def remove_table_from_list(app_token):
    """从列表中删除表格"""
    table_list = load_table_list()
    table_list = [t for t in table_list if t['app_token'] != app_token]
    save_table_list(table_list)

import yaml
import os
import json
import time
import sqlite3
import atexit
import threading
from datetime import datetime, timedelta
from streamlit_modal import Modal
from adreambox_client import AdreamboxClient
from feed_parser import parse_feeds_html
from tagger import tag_feed

# ============ 定时任务后台线程 ============
class ScheduleManager:
    """定时任务管理器"""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.running = False
        self.thread = None
        self.status = {
            "is_running": False,
            "last_run": None,
            "next_run": None,
            "progress": None,
            "message": "",
            "error": None
        }
        self._stop_event = threading.Event()
        
    def start(self):
        """启动定时任务监控线程"""
        if self.thread is None or not self.thread.is_alive():
            self._stop_event.clear()
            self.thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self.thread.start()
            self.running = True
    
    def stop(self):
        """停止定时任务监控线程"""
        self._stop_event.set()
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
    
    def _monitor_loop(self):
        """监控循环：检查定时任务并执行"""
        while not self._stop_event.is_set():
            try:
                self._check_and_run_schedule()
            except Exception as e:
                self.status["error"] = str(e)
            # 每 30 秒检查一次
            self._stop_event.wait(30)
    
    def _check_and_run_schedule(self):
        """检查并执行定时任务"""
        config = load_config()
        schedule_config = config.get("schedule", {})
        
        if not schedule_config.get("enabled"):
            return
        
        # 计算下次执行时间
        frequency = schedule_config.get("frequency", "每天")
        time_str = schedule_config.get("time", "20:00")
        
        try:
            hour, minute = map(int, time_str.split(":"))
        except:
            return
        
        now = datetime.now()
        
        # 计算下次执行时间
        if frequency == "每天":
            next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
        elif frequency == "每周":
            next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            days_ahead = 7 - now.weekday()
            if days_ahead == 7:
                days_ahead = 0
            next_run += timedelta(days=days_ahead)
            if next_run <= now:
                next_run += timedelta(days=7)
        else:  # 每月
            next_run = now.replace(day=1, hour=hour, minute=minute, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=30)
        
        self.status["next_run"] = next_run.strftime("%Y-%m-%d %H:%M")
        
        # 检查是否到达执行时间（允许 1 分钟误差）
        if abs((now - next_run).total_seconds()) < 60:
            if not self.status["is_running"]:
                self._run_schedule_task(config, schedule_config)
    
    def _run_schedule_task(self, config, schedule_config):
        """执行定时采集任务"""
        self.status["is_running"] = True
        self.status["progress"] = 0
        self.status["message"] = "定时采集开始..."
        self.status["error"] = None
        
        try:
            # 解析配置
            school_mode = schedule_config.get("school_mode", "全部学校")
            selected_schools = schedule_config.get("selected_schools", [])
            depth = schedule_config.get("depth", "最近 30 条/校")
            image_strategy = schedule_config.get("image_strategy", "仅保存图片链接")
            fetch_detail = schedule_config.get("fetch_detail", True)
            tag_filter = schedule_config.get("tag_filter", [])
            word_tier_filter = schedule_config.get("word_tier_filter", [])
            
            # 转换 school_mode 为 run_crawl 期望的格式
            crawl_school_mode = "全部" if school_mode == "全部学校" else "选择"
            
            # 获取学校列表
            if crawl_school_mode == "全部":
                schools = get_schools_cache()
                if not schools:
                    client = AdreamboxClient()
                    schools = client.get_all_schools()
                    save_schools_cache(schools)
            else:
                # selected_schools 保存的是学校对象列表
                schools = selected_schools
            
            self.status["message"] = f"正在采集 {len(schools)} 所学校..."
            
            # 调用采集函数
            start_time = time.time()
            
            # 由于 Streamlit 的限制，后台线程无法使用 st.progress 等 UI 组件
            # 我们直接调用 run_crawl，它会自动记录到采集日志
            try:
                result = run_crawl(
                    school_mode=crawl_school_mode,
                    selected_schools=schools,
                    depth=depth,
                    depth_custom=None,
                    image_strategy=image_strategy,
                    tag_filter=tag_filter,
                    word_tier_filter=word_tier_filter,
                    date_start=None,
                    date_end=None,
                    fetch_detail=fetch_detail
                )
                
                if result and len(result) >= 3:
                    total_new, total_skip, total_error = result[0], result[1], result[2]
                    duration = int(time.time() - start_time)
                    
                    self.status["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                    self.status["message"] = f"定时采集完成：新增 {total_new} 条，跳过 {total_skip} 条，失败 {total_error} 条"
                else:
                    self.status["message"] = "定时采集完成"
                    self.status["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                    
            except Exception as e:
                self.status["error"] = str(e)
                self.status["message"] = f"定时采集失败：{str(e)}"
        
        except Exception as e:
            self.status["error"] = str(e)
            self.status["message"] = f"定时采集失败：{str(e)}"
        finally:
            self.status["is_running"] = False
            self.status["progress"] = None

# 全局定时任务管理器
schedule_manager = ScheduleManager()
from dedup import DedupManager
from feishu_writer import FeishuWriter

# 全局变量，用于在程序退出时记录日志
_crawl_state = {
    "active": False,
    "school_mode": "",
    "school_count": 0,
    "new_count": 0,
    "skip_count": 0,
    "error_count": 0,
    "feed_error_count": 0,
    "feed_warning_count": 0,
    "start_time": None,
    "config": {},
    "error_details": "",
    "skipped_details": "",
    "summary_msg": "",
    "feed_error_details": "",
    "feed_warning_details": ""
}

def _save_crawl_log_on_exit():
    if _crawl_state["active"] and _crawl_state["start_time"]:
        duration = (datetime.now() - _crawl_state["start_time"]).total_seconds()
        try:
            log_crawl(
                _crawl_state["school_mode"],
                _crawl_state["school_count"],
                _crawl_state["new_count"],
                _crawl_state["skip_count"],
                _crawl_state["error_count"],
                duration,
                _crawl_state["config"],
                _crawl_state["error_details"],
                _crawl_state["skipped_details"],
                _crawl_state["summary_msg"] + " (程序异常退出)",
                _crawl_state["feed_error_details"],
                _crawl_state["feed_warning_details"]
            )
            print("[日志] 程序退出时已保存采集日志")
        except Exception as e:
            print(f"[日志] 保存日志失败：{e}")

atexit.register(_save_crawl_log_on_exit)
from feishu_config import FeishuConfigManager
import pandas as pd
from fpdf import FPDF
import requests
from PIL import Image
from io import BytesIO

st.set_page_config(page_title="梦想盒子素材采集工具", layout="wide", page_icon="")

CONFIG_PATH = "config.yaml"
DB_PATH = "data/collected.db"
CRAWL_LOG_DB_PATH = "data/crawl_log.db"

# 飞书配置管理器（使用飞书表格作为配置数据库）
# 注意：这里使用硬编码的初始凭证来启动，后续配置会从飞书表格读取
INITIAL_APP_ID = "cli_你的应用ID"
INITIAL_APP_SECRET = "你的应用密钥"
INITIAL_APP_TOKEN = "你的表格AppToken"
INITIAL_CONFIG_TABLE_ID = "tbl0SuteTNP5Y518"  # 配置表 ID（系统配置表）

# 全局配置管理器实例
_config_manager = None


def get_config_manager():
    """获取配置管理器实例"""
    # 优先级：1.界面输入 (session_state) → 2.st.secrets → 3. 环境变量 → 4. 默认值
    
    # 1. 优先从 session_state 读取（界面输入）
    app_id = st.session_state.get("app_id", "")
    app_secret = st.session_state.get("app_secret", "")
    app_token = st.session_state.get("app_token", "")
    config_table_id = st.session_state.get("config_table_id", "")
    
    # 2. 如果界面没填，从 st.secrets 读取
    if not app_id:
        app_id = st.secrets.get("feishu", {}).get("app_id", "")
    if not app_secret:
        app_secret = st.secrets.get("feishu", {}).get("app_secret", "")
    if not app_token:
        app_token = st.secrets.get("feishu", {}).get("app_token", "")
    if not config_table_id:
        config_table_id = st.secrets.get("feishu", {}).get("config_table_id", "")
    
    # 3. 如果还没有，从环境变量读取
    if not app_id:
        app_id = os.environ.get("FEISHU_APP_ID", "")
    if not app_secret:
        app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    if not app_token:
        app_token = os.environ.get("FEISHU_APP_TOKEN", "")
    if not config_table_id:
        config_table_id = os.environ.get("FEISHU_CONFIG_TABLE_ID", INITIAL_CONFIG_TABLE_ID)
    
    # 4. 最后才用默认值
    if not app_id:
        app_id = INITIAL_APP_ID
    if not app_secret:
        app_secret = INITIAL_APP_SECRET
    if not app_token:
        app_token = INITIAL_APP_TOKEN
    
    return FeishuConfigManager(app_id, app_secret, app_token, config_table_id)


# ============ 配置管理（本地文件） ============
CONFIG_FILE = "data/config.json"

def load_config():
    """从本地文件加载配置"""
    # 优先从本地文件读取
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # 合并 session_state 中的凭证（界面输入优先）
            if "feishu" not in config:
                config["feishu"] = {}
            
            if st.session_state.get("app_id"):
                config["feishu"]["app_id"] = st.session_state.app_id
            if st.session_state.get("app_secret"):
                config["feishu"]["app_secret"] = st.session_state.app_secret
            if st.session_state.get("app_token"):
                config["feishu"]["app_token"] = st.session_state.app_token
            if st.session_state.get("table_id"):
                config["feishu"]["table_id"] = st.session_state.table_id
            
            return config
        except Exception:
            pass
    
    # 本地文件不存在或读取失败，使用 secrets.toml 中的凭证
    return {
        "feishu": {
            "app_id": st.secrets.get("feishu", {}).get("app_id", INITIAL_APP_ID),
            "app_secret": st.secrets.get("feishu", {}).get("app_secret", INITIAL_APP_SECRET),
            "app_token": st.secrets.get("feishu", {}).get("app_token", INITIAL_APP_TOKEN),
            "table_id": st.secrets.get("feishu", {}).get("table_id", ""),
        },
        "crawl": {
            "request_interval": 0.5,
            "max_per_school": 0,
        },
        "auth": {
            "enabled": False,
            "password": "",
        }
    }


def save_config(config):
    """保存配置到本地文件"""
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False




def check_auth():
    """检查登录认证（支持 session_state 覆盖）"""
    # 优先检查 session_state 中的覆盖值
    if 'auth_override' in st.session_state:
        if not st.session_state['auth_override']:
            return True  # 用户已取消启用
    
    config = load_config()
    auth_cfg = config.get("auth", {})
    
    if not auth_cfg.get("enabled", False):
        return True
    
    # 检查认证状态
    if st.session_state.get('authenticated', False):
        return True
    
    # 从飞书表格读取密码
    password = auth_cfg.get("password", "")
    if password:
        input_password = st.text_input("请输入访问密码", type="password", key="auth_password_input")
        if input_password != password:
            st.error("密码错误")
            st.stop()
        st.session_state['authenticated'] = True
        return True
    
    return True


def get_schools_cache():
    """获取学校列表（带缓存）"""
    cache_file = "data/schools_cache.json"
    if os.path.exists(cache_file):
        with open(cache_file, "r") as f:
            return json.load(f)
    return []


def save_schools_cache(schools):
    """保存学校列表缓存"""
    os.makedirs("data", exist_ok=True)
    with open("data/schools_cache.json", "w") as f:
        json.dump(schools, f, ensure_ascii=False)


def init_crawl_log():
    """初始化采集日志数据库"""
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS crawl_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            school_mode TEXT,
            school_count INTEGER,
            new_count INTEGER,
            skip_count INTEGER,
            error_count INTEGER,
            duration_seconds REAL,
            config TEXT,
            error_details TEXT,
            skipped_details TEXT,
            summary_msg TEXT,
            feed_error_details TEXT,
            feed_warning_details TEXT
        )
    ''')
    # 为已存在的数据库添加 summary_msg 列（如果不存在）
    try:
        cursor.execute('ALTER TABLE crawl_log ADD COLUMN summary_msg TEXT')
    except:
        pass  # 列已存在
    # 为已存在的数据库添加 feed_error_details 列（如果不存在）
    try:
        cursor.execute('ALTER TABLE crawl_log ADD COLUMN feed_error_details TEXT')
    except:
        pass  # 列已存在
    # 为已存在的数据库添加 feed_warning_details 列（如果不存在）
    try:
        cursor.execute('ALTER TABLE crawl_log ADD COLUMN feed_warning_details TEXT')
    except:
        pass  # 列已存在
    # 为已存在的数据库添加 school_details 列（如果不存在）
    try:
        cursor.execute('ALTER TABLE crawl_log ADD COLUMN school_details TEXT')
    except:
        pass  # 列已存在
    conn.commit()
    conn.close()


def log_crawl(school_mode, school_count, new_count, skip_count, error_count, duration, config, error_details="", skipped_details="", summary_msg="", feed_error_details="", feed_warning_details="", school_details=""):
    """记录采集日志"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO crawl_log (timestamp, school_mode, school_count, new_count, skip_count, error_count, duration_seconds, config, error_details, skipped_details, summary_msg, feed_error_details, feed_warning_details, school_details)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (datetime.now().isoformat(), school_mode, school_count, new_count, skip_count, error_count, duration, json.dumps(config, ensure_ascii=False), error_details, skipped_details, summary_msg, feed_error_details, feed_warning_details, school_details))
    conn.commit()
    conn.close()


def get_crawl_logs(limit=50):
    """获取采集日志"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM crawl_log ORDER BY id DESC LIMIT ?', (limit,))
    columns = [description[0] for description in cursor.description]
    logs = [dict(zip(columns, row)) for row in cursor.fetchall()]
    conn.close()
    return logs


def download_image(url):
    """下载图片"""
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        return None


def upload_image_to_feishu(feishu_writer, image_data, filename):
    """上传图片到飞书附件"""
    # 这里需要实现飞书文件上传 API
    # 暂时返回占位符
    return f"uploaded_{filename}"


def export_to_excel(data, filename="export.xlsx"):
    """导出到 Excel"""
    df = pd.DataFrame(data)
    df.to_excel(filename, index=False, engine='openpyxl')
    return filename


def export_to_pdf(data, filename="export.pdf"):
    """导出到 PDF"""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    
    # 添加标题
    pdf.cell(200, 10, txt="梦想盒子素材采集报告", ln=True, align='C')
    pdf.ln(10)
    
    # 添加数据
    for item in data:
        pdf.cell(200, 10, txt=f"学校：{item.get('school_name', '')}", ln=True)
        pdf.cell(200, 10, txt=f"发布时间：{item.get('publish_time', '')}", ln=True)
        pdf.cell(200, 10, txt=f"素材类型：{item.get('material_type', '')}", ln=True)
        pdf.multi_cell(200, 10, txt=f"内容：{item.get('content', '')[:200]}")
        pdf.ln(5)
    
    pdf.output(filename)
    return filename


def run_crawl_with_retry(client, sid, school_name, max_items, max_retries=3):
    """带重试的采集函数，返回 (feeds_html, error_type)"""
    for attempt in range(max_retries):
        try:
            feeds_html = client.get_school_feeds(sid, max_items=max_items)
            if feeds_html:
                return feeds_html, None  # 成功
            else:
                return None, "no_feeds"  # API 返回空，该校无动态
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            else:
                return None, f"network_error: {str(e)}"  # 网络错误
    return None, "unknown"  # 未知错误


def run_crawl(school_mode, selected_schools, depth, depth_custom, image_strategy, tag_filter, word_tier_filter=None, date_start=None, date_end=None, fetch_detail=False):
    """执行采集（饱和采集模式：目标入库条数）"""
    if word_tier_filter is None:
        word_tier_filter = []
    start_time = datetime.now()
    config = load_config()
    feishu_cfg = config.get("feishu", {})
    
    # 优先使用 session_state 中的凭证（界面输入），否则用配置文件的
    app_id = st.session_state.get("app_id", "") or feishu_cfg.get("app_id", "")
    app_secret = st.session_state.get("app_secret", "") or feishu_cfg.get("app_secret", "")
    app_token = st.session_state.get("app_token", "") or feishu_cfg.get("app_token", "")
    table_id = st.session_state.get("table_id", "") or feishu_cfg.get("table_id", "")

    client = AdreamboxClient()
    dedup = DedupManager()
    writer = FeishuWriter(
        app_id=app_id,
        app_secret=app_secret,
        app_token=app_token,
        table_id=table_id,
    )
    writer.ensure_fields()

    # 确定学校列表
    if school_mode == "全部":
        schools = client.get_all_schools()
        save_schools_cache(schools)
    elif school_mode == "选择":
        schools = selected_schools
    else:
        schools = []

    # 计算处理条数（严格模式：设置N条就只处理N条）
    target_count = 0
    if isinstance(depth, int):
        target_count = depth
    elif depth == "最近 30 条/校":
        target_count = 30
    elif depth == "最近 100 条/校":
        target_count = 100
    elif depth == "最近 300 条/校":
        target_count = 300
    # 全量采集或按时间范围时，target_count=0 表示不限制
    
    # 安全阀：最大抓取上限（防止死循环）
    MAX_FETCH_PER_SCHOOL = 200  # 每校最多抓取200条动态

    total_new = 0
    total_skip = 0
    total_error = 0
    total_feeds = 0
    total_filtered = 0  # 时间范围过滤的数量
    total_feed_error = 0  # 动态级失败计数（写入失败）
    total_feed_warning = 0  # 动态级警告计数（详情获取失败）
    error_details_list = []  # 收集错误详情（学校级）
    feed_error_list = []  # 收集动态级失败详情
    feed_warning_list = []  # 收集动态级警告详情
    skipped_list = []  # 收集跳过记录
    school_details = []  # 收集每所学校处理详情

    # 记录开始时间，用于异常时也能记录日志
    start_time = datetime.now()
    crawl_start_time = start_time.isoformat()
    
    # 设置全局状态（用于程序退出时记录日志）
    _crawl_state["active"] = True
    _crawl_state["school_mode"] = school_mode
    _crawl_state["school_count"] = len(schools)
    _crawl_state["start_time"] = start_time
    _crawl_state["config"] = config

    # 暂停状态
    if 'crawl_paused' not in st.session_state:
        st.session_state.crawl_paused = False
    if 'crawl_cancelled' not in st.session_state:
        st.session_state.crawl_cancelled = False

    progress_bar = st.progress(0)
    status_text = st.empty()
    stats_text = st.empty()
    
    # 暂停/取消按钮
    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        pause_btn = st.button("⏸️ 暂停采集", key="pause_btn", use_container_width=True)
    with btn_col2:
        cancel_btn = st.button("⏹️ 取消采集", key="cancel_btn", use_container_width=True)
    
    if pause_btn:
        st.session_state.crawl_paused = not st.session_state.crawl_paused
        if st.session_state.crawl_paused:
            st.warning("⏸️ 采集已暂停，点击'继续采集'恢复")
            st.session_state.crawl_cancelled = False
        else:
            st.success("▶️ 采集已恢复")
        st.rerun()
    
    if cancel_btn:
        st.session_state.crawl_cancelled = True
        st.session_state.crawl_paused = False
        st.warning("⏹️ 采集已取消")

    for i, school in enumerate(schools):
        # 检查是否取消
        if st.session_state.get('crawl_cancelled', False):
            st.warning("⏹️ 采集已取消")
            break
        
        # 检查是否暂停
        if st.session_state.get('crawl_paused', False):
            st.warning("⏸️ 采集已暂停")
            # 显示暂停界面
            pause_col1, pause_col2 = st.columns(2)
            with pause_col1:
                if st.button("▶️ 继续采集", key="resume_btn", use_container_width=True, type="primary"):
                    st.session_state.crawl_paused = False
                    st.session_state.crawl_cancelled = False
                    st.rerun()
            with pause_col2:
                if st.button("⏹️ 取消采集", key="cancel_btn_paused", use_container_width=True):
                    st.session_state.crawl_cancelled = True
                    st.session_state.crawl_paused = False
                    st.warning("⏹️ 采集已取消")
                    break
            st.stop()
        
        sid = school["sid"]
        school_name = school.get("name", f"学校{sid}")
        status_text.text(f"正在采集：{school_name} ({i+1}/{len(schools)})")

        school_collected = 0  # 当前学校已入库条数
        school_fetched = 0    # 当前学校已抓取条数

        try:
            # 严格模式：设置N条就只处理N条动态
            if target_count > 0:
                fetch_limit = min(target_count, MAX_FETCH_PER_SCHOOL)
            else:
                fetch_limit = MAX_FETCH_PER_SCHOOL
            
            # 带重试的采集
            feeds_html, error_type = run_crawl_with_retry(client, sid, school_name, fetch_limit)
            if not feeds_html:
                total_error += 1
                if error_type == "no_feeds":
                    error_msg = f"{school_name}: 采集失败（该校没有动态）"
                elif error_type and error_type.startswith("network_error"):
                    error_msg = f"{school_name}: 采集失败（网络连接异常：{error_type.replace('network_error: ', '')}）"
                else:
                    error_msg = f"{school_name}: 采集失败（原因未知）"
                error_details_list.append(error_msg)
                st.warning(f"️ {error_msg}")
                
                # 记录学校详情
                school_details.append({
                    'name': school_name,
                    'status': 'failed',
                    'reason': error_msg,
                    'collected': 0,
                    'fetched': 0
                })
                continue
                
            print(f"[采集] {school_name} - 开始解析 HTML（共{len(feeds_html)}字符）")
            all_feeds = parse_feeds_html(feeds_html, sid, school_name)
            print(f"[采集] {school_name} - 解析完成（共{len(all_feeds)}条动态）")
            
            # 去除重复的 feed_id（API 可能返回重复数据）
            seen_ids = set()
            unique_feeds = []
            for feed in all_feeds:
                if feed["feed_id"] not in seen_ids:
                    seen_ids.add(feed["feed_id"])
                    unique_feeds.append(feed)
            all_feeds = unique_feeds

            # 按时间范围过滤
            if date_start and date_end:
                filtered_feeds = []
                for feed in all_feeds:
                    pub_time_str = feed.get("pub_time", "")
                    if pub_time_str:
                        try:
                            pub_time = datetime.strptime(pub_time_str, "%Y-%m-%d %H:%M")
                            if date_start <= pub_time.date() <= date_end:
                                filtered_feeds.append(feed)
                            else:
                                total_filtered += 1
                        except:
                            filtered_feeds.append(feed)  # 解析失败则保留
                    else:
                        filtered_feeds.append(feed)  # 无时间则保留
                all_feeds = filtered_feeds
            
            # 记录过滤数量
            filtered_count = len(all_feeds) - len([f for f in all_feeds if f.get('pub_time')])
            
            # 记录学校详情
            school_details.append({
                'name': school_name,
                'status': 'success',
                'collected': 0,  # 稍后更新
                'fetched': len(all_feeds),
                'filtered': len(all_feeds) - len([f for f in all_feeds if f.get('pub_time')])
            })

            for feed in all_feeds:
                total_feeds += 1
                # 自动去重
                if dedup.is_collected(feed["feed_id"]):
                    total_skip += 1
                    collected_at = dedup.get_collected_time(feed["feed_id"])
                    skipped_list.append({
                        "feed_id": feed["feed_id"],
                        "school_name": school_name,
                        "collected_at": collected_at,
                        "reason": "已采集过（去重）"
                    })
                    continue

                tag_result = tag_feed(feed)
                feed["material_type"] = tag_result["material_type"]
                feed["value_tag"] = tag_result["value_level"]
                feed["word_tier"] = tag_result.get("word_tier", "")

                # 素材类型过滤
                if tag_filter and tag_result["material_type"] not in tag_filter:
                    total_skip += 1
                    skipped_list.append({
                        "feed_id": feed["feed_id"],
                        "school_name": school_name,
                        "collected_at": None,
                        "reason": "不符合素材类型"
                    })
                    continue
                
                # 字数档位过滤（仅对图文混合类型）
                if tag_result["material_type"] == "图文混合" and word_tier_filter:
                    if tag_result.get("word_tier", "") not in word_tier_filter:
                        total_skip += 1
                        skipped_list.append({
                            "feed_id": feed["feed_id"],
                            "school_name": school_name,
                            "collected_at": None,
                            "reason": f"不符合字数档位（当前：{tag_result.get('word_tier', '')}）"
                        })
                        continue

                # 图片处理
                if image_strategy == "下载原图到飞书附件" and feed.get("images"):
                    for img_url in feed["images"]:
                        img_data = download_image(img_url)
                        if img_data:
                            # 上传到飞书（需要实现）
                            pass

                # 获取完整内容（如果需要）
                if fetch_detail and feed.get("detail_url"):
                    try:
                        detail_text = client.get_feed_detail(feed["detail_url"])
                        if detail_text and len(detail_text) > len(feed.get("text_content", "")):
                            feed["text_content"] = detail_text
                            # 重新打标
                            tag_result = tag_feed(feed)
                            feed["material_type"] = tag_result["material_type"]
                            feed["value_tag"] = tag_result["value_level"]
                            feed["word_tier"] = tag_result.get("word_tier", "")
                    except Exception as e:
                        # 详情页获取失败，保留列表页摘要，记录警告
                        total_feed_warning += 1
                        feed_warning_list.append({
                            "feed_id": feed["feed_id"],
                            "school_name": school_name,
                            "warning_reason": "详情页获取失败，仅保留列表页摘要",
                            "detail_url": feed.get("detail_url", "")
                        })
                        print(f"[警告] {school_name} - 动态{feed['feed_id']}详情页获取失败，仅保留摘要")

                # 实时日志输出
                print(f"[采集] {school_name} - 正在写入：{feed['feed_id']}")
                
                # 单条动态写入失败处理
                try:
                    writer.write_feed(feed)
                    dedup.mark_collected([feed])
                    total_new += 1
                    school_collected += 1
                    # 更新 school_details 中的 collected 字段
                    if school_details and school_details[-1]['name'] == school_name:
                        school_details[-1]['collected'] = school_collected
                    print(f"[采集] {school_name} - 写入成功（累计：{school_collected}条）")
                except Exception as e:
                    total_feed_error += 1
                    error_reason = str(e)
                    # 简化错误信息
                    if "写入记录失败" in error_reason:
                        error_reason = "飞书写入失败"
                    elif "timeout" in error_reason.lower() or "Timeout" in error_reason:
                        error_reason = "请求超时"
                    elif "Connection" in error_reason or "connect" in error_reason:
                        error_reason = "网络连接失败"
                    
                    feed_error_list.append({
                        "feed_id": feed["feed_id"],
                        "school_name": school_name,
                        "error_reason": error_reason,
                        "detail_url": feed.get("detail_url", "")
                    })
                    print(f"[失败] {school_name} - 动态{feed['feed_id']}写入失败：{error_reason}")
                
                # 更新全局状态
                _crawl_state["new_count"] = total_new
                _crawl_state["skip_count"] = total_skip
                _crawl_state["error_count"] = total_error
                _crawl_state["feed_error_count"] = total_feed_error
                _crawl_state["feed_warning_count"] = total_feed_warning
                _crawl_state["feed_error_details"] = json.dumps(feed_error_list, ensure_ascii=False) if feed_error_list else ""
                _crawl_state["feed_warning_details"] = json.dumps(feed_warning_list, ensure_ascii=False) if feed_warning_list else ""

        except Exception as e:
            total_error += 1
            error_msg = f"{school_name}: {str(e)}"
            error_details_list.append(error_msg)
            st.warning(f"采集 {school_name} 失败：{e}")

        # 更新进度
        progress = (i + 1) / len(schools)
        progress_bar.progress(progress)
        st.session_state.crawl_completed_schools = i + 1
        
        # 计算速度和预计剩余时间
        elapsed = (datetime.now() - start_time).total_seconds()
        if elapsed > 0:
            speed = total_feeds / elapsed
            remaining_schools = len(schools) - i - 1
            avg_time_per_school = elapsed / (i + 1)
            estimated_remaining = avg_time_per_school * remaining_schools
            
            stats_text.info(
                f"📊 进度：{i+1}/{len(schools)} 学校 | "
                f"新增：{total_new} 条 | 跳过：{total_skip} 条 | 学校失败：{total_error} 所 | "
                f"动态失败：{total_feed_error} 条 | 动态警告：{total_feed_warning} 条 | "
                f"速度：{speed:.1f}条/秒 | 预计剩余：{estimated_remaining/60:.1f}分钟"
            )

    duration = (datetime.now() - start_time).total_seconds()
    status_text.text("✅ 采集完成！")
    
    # 更新 school_details 中的 collected 字段（使用全局统计，因为单学校统计在循环中难以精确追踪）
    # 注意：这里的 collected 是全局累计值，不是单学校的
    # 如果需要精确的单学校统计，需要在循环中每次成功写入后更新 school_details
    
    # 生成详细统计提示
    summary_msg = (
        f"✅ 完成！共采集 {len(schools)} 所学校，"
        f"新增 {total_new} 条，跳过 {total_skip} 条，"
        f"学校失败 {total_error} 所，动态失败 {total_feed_error} 条，"
        f"动态警告 {total_feed_warning} 条（详情页获取失败，仅保留摘要），"
        f"耗时 {duration:.1f} 秒"
    )
    
    # 添加时间过滤信息
    if total_filtered > 0:
        summary_msg += f"，时间过滤 {total_filtered} 条"
    
    # 如果新增+跳过+失败 < 目标条数，说明该校总共就只有这么多动态
    if target_count > 0:
        total_processed = total_new + total_skip + total_error
        target_total = target_count * len(schools)
        if total_processed < target_total:
            summary_msg += (
                f"\n\n**统计说明：**\n"
                f"• 设置处理：{target_total} 条\n"
                f"• 实际处理：{total_processed} 条\n"
                f"• **该校总共就只有 {total_processed} 条动态**（已全部抓取完毕）"
            )
    
    stats_text.info(summary_msg)
    
    # 记录日志
    error_details_str = "\n".join(error_details_list) if error_details_list else ""
    skipped_details_str = json.dumps(skipped_list, ensure_ascii=False) if skipped_list else ""
    feed_error_details_str = json.dumps(feed_error_list, ensure_ascii=False) if feed_error_list else ""
    feed_warning_details_str = json.dumps(feed_warning_list, ensure_ascii=False) if feed_warning_list else ""
    school_details_str = json.dumps(school_details, ensure_ascii=False) if school_details else ""
    log_crawl(school_mode, len(schools), total_new, total_skip, total_error, duration, config, error_details_str, skipped_details_str, summary_msg, feed_error_details_str, feed_warning_details_str, school_details_str)
    

    return total_new, total_skip, total_error, skipped_list, total_filtered, error_details_list, total_feed_error, feed_error_list, total_feed_warning, feed_warning_list, school_details


def main():
    # 认证检查
    check_auth()
    
    # 启动定时任务管理器
    schedule_manager.start()
    
    st.title("📦 梦想盒子素材采集工具")

    config = load_config()

    tab1, tab2, tab3, tab4 = st.tabs([
        "🎯 采集控制", 
        "📊 数据看板", 
        "📝 采集日志",
        "⚙️ 设置"
    ])

    with tab1:
        st.subheader("采集配置")

        col1, col2 = st.columns(2)

        with col1:
            school_mode = st.radio("学校范围", ["全部", "选择"], horizontal=True)
            
            if school_mode == "选择":
                # 获取学校列表
                schools = get_schools_cache()
                
                # 刷新按钮
                col_refresh1, col_refresh2 = st.columns([4, 1])
                with col_refresh2:
                    if st.button("🔄 刷新", help="重新获取最新学校列表"):
                        schools = None
                        save_schools_cache([])
                        st.rerun()
                
                if not schools:
                    with st.spinner("正在获取学校列表..."):
                        client = AdreamboxClient()
                        schools = client.get_all_schools()
                        save_schools_cache(schools)
                
                # 学校搜索
                school_search = st.text_input(
                    "搜索学校",
                    placeholder="输入学校名称关键词...",
                    help="输入关键词过滤学校列表",
                    key="manual_school_search"
                )
                
                # 学校多选
                school_options = {f"{s['name']} (SID: {s['sid']})": s for s in schools}
                
                # 根据搜索关键词过滤学校列表（支持名称和 ID 搜索）
                if school_search:
                    filtered_options = {k: v for k, v in school_options.items() 
                                       if school_search.lower() in k.lower() 
                                       or school_search in str(v.get('sid', ''))}
                else:
                    filtered_options = school_options
                
                # 保持已选学校（更可靠的逻辑）
                if 'manual_selected_schools' not in st.session_state:
                    st.session_state.manual_selected_schools = []
                
                # 已选学校始终在选项中（即使不在搜索结果中）
                display_options = dict(filtered_options)
                for k in st.session_state.manual_selected_schools:
                    if k not in display_options and k in school_options:
                        display_options[k] = school_options[k]
                
                # 获取 multiselect 当前值（如果存在）
                current_widget_value = st.session_state.get("manual_school_multiselect", [])
                
                # 确保 default 值在 options 中
                valid_defaults = [v for v in current_widget_value if v in display_options]
                if not valid_defaults:
                    valid_defaults = [v for v in st.session_state.manual_selected_schools if v in display_options]
                
                selected_names = st.multiselect(
                    "选择学校（可多选）",
                    options=list(display_options.keys()),
                    default=valid_defaults,
                    help="支持搜索和多选，切换搜索关键词时保留已选学校",
                    key="manual_school_multiselect"
                )
                
                # 直接使用 multiselect 的当前值（不合并历史值）
                st.session_state.manual_selected_schools = list(selected_names) if selected_names else []
                
                selected_schools = [school_options[name] for name in st.session_state.manual_selected_schools if name in school_options]
            else:
                selected_schools = []

            # 采集深度模式选择
            depth_mode = st.radio(
                "采集深度模式",
                ["按条数限制", "按时间范围"],
                horizontal=True,
                help="选择采集范围的限制方式"
            )
            
            depth = None
            depth_custom = 0
            date_start = None
            date_end = None
            
            if depth_mode == "按条数限制":
                depth_options = ["全量采集", "最近 30 条/校", "最近 100 条/校", "最近 300 条/校", "自定义条数"]
                depth = st.selectbox(
                    "采集条数",
                    depth_options,
                    help="选择每校采集的条数"
                )
                
                if depth == "自定义条数":
                    depth_custom = st.number_input(
                        "每校采集条数",
                        min_value=1,
                        max_value=1000,
                        value=100,
                        step=10
                    )
                    depth = depth_custom  # 将自定义条数传给 depth
            else:  # 按时间范围
                col_date1, col_date2 = st.columns(2)
                with col_date1:
                    date_start = st.date_input(
                        "开始日期",
                        value=datetime.now() - timedelta(days=30),
                        help="采集该日期之后的动态"
                    )
                with col_date2:
                    date_end = st.date_input(
                        "结束日期",
                        value=datetime.now(),
                        help="采集该日期之前的动态"
                    )

        with col2:
            image_strategy = st.radio(
                "图片处理策略",
                ["仅保存 URL", "下载原图到飞书附件"],
                help="选择图片处理方式"
            )
            
            st.markdown("**素材类型筛选（可交叉组合）：**")
            tag_mixed = st.checkbox("图文混合", value=True, key="manual_tag_mixed")
            tag_image = st.checkbox("纯图片", key="manual_tag_image")
            tag_text_only = st.checkbox("纯文字", key="manual_tag_text")
            
            # 图文混合的下一级选项：字数档位
            word_tier_filter = []
            if tag_mixed:
                st.markdown("&nbsp;&nbsp;&nbsp;&nbsp;**内容长度（仅图文混合）：**")
                col_t1, col_t2, col_t3, col_t4 = st.columns(4)
                with col_t1:
                    tier_short = st.checkbox("短(<30字)", value=True, key="manual_tier_short")
                with col_t2:
                    tier_mid_short = st.checkbox("中短(30-50字)", value=True, key="manual_tier_mid_short")
                with col_t3:
                    tier_mid_long = st.checkbox("中长(50-100字)", value=True, key="manual_tier_mid_long")
                with col_t4:
                    tier_long = st.checkbox("长(≥100字)", value=True, key="manual_tier_long")
                
                if tier_short:
                    word_tier_filter.append("短")
                if tier_mid_short:
                    word_tier_filter.append("中短")
                if tier_mid_long:
                    word_tier_filter.append("中长")
                if tier_long:
                    word_tier_filter.append("长")

        tag_filter = []
        if tag_mixed:
            tag_filter.append("图文混合")
        if tag_image:
            tag_filter.append("纯图片")
        if tag_text_only:
            tag_filter.append("纯文字")

        # 获取完整内容选项
        fetch_detail = st.checkbox(
            "获取完整内容（访问详情页）",
            value=True,
            help="列表页只显示前 100 字摘要，勾选后会访问每条动态的详情页获取完整内容（采集速度会变慢）"
        )

        # 保存筛选条件到 session_state，供 Tab 2 预览使用
        st.session_state['tag_filter'] = tag_filter
        st.session_state['word_tier_filter'] = word_tier_filter
        st.session_state['school_mode'] = school_mode
        st.session_state['selected_schools'] = selected_schools

        st.markdown("---")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("▶ 立即采集", type="primary", use_container_width=True):
                with st.spinner("采集中，请稍候..."):
                    result = run_crawl(
                        school_mode, selected_schools, depth, depth_custom, image_strategy, tag_filter, word_tier_filter,
                        date_start=date_start, date_end=date_end, fetch_detail=fetch_detail
                    )
                    new_count, skip_count, error_count, skipped_list, filtered_count, error_details_list, feed_error_count, feed_error_list, feed_warning_count, feed_warning_list, school_details = result
                success_msg = f"采集完成！新增 {new_count} 条，跳过 {skip_count} 条，学校失败 {error_count} 所，动态失败 {feed_error_count} 条，动态警告 {feed_warning_count} 条"
                if filtered_count > 0:
                    success_msg += f"，时间过滤 {filtered_count} 条"
                st.success(success_msg)
                
                # 显示跳过记录（始终显示）
                with st.expander(f"📋 查看跳过记录（{len(skipped_list)}条）"):
                    if skipped_list:
                        for item in skipped_list:
                            reason = item.get("reason", "已采集过")
                            collected_at = item.get("collected_at") or "未知"
                            st.write(
                                f"- **动态 ID**: {item['feed_id']} | "
                                f"**学校**: {item['school_name']} | "
                                f"**上次采集**: {collected_at} | "
                                f"**原因**: {reason}"
                            )
                    else:
                        st.info("无跳过记录")
                
                # 显示失败记录（始终显示）
                with st.expander(f" 查看失败记录（{len(error_details_list)}条）"):
                    if error_details_list:
                        for item in error_details_list:
                            st.write(f"- {item}")
                    else:
                        st.info("无失败记录")
                st.rerun()

        with col2:
            feishu_cfg = config.get("feishu", {})
            _app_token = feishu_cfg.get("app_token", "")
            _table_id = feishu_cfg.get("table_id", "")
            feishu_url = f"https://xxx.feishu.cn/base/{_app_token}?table={_table_id}" if _app_token and _table_id else "#"
            st.link_button(
                "📋 查看飞书表格 →",
                feishu_url,
                use_container_width=True,
                disabled=not (_app_token and _table_id),
            )

        st.markdown("---")
        st.subheader("定时采集")

        # 定时任务状态显示
        if schedule_manager.status["is_running"]:
            st.info(f"🔄 **定时采集正在执行** - {schedule_manager.status['message']}")
        elif schedule_manager.status["last_run"]:
            st.success(f"✅ 上次执行：{schedule_manager.status['last_run']} - {schedule_manager.status['message']}")
        
        if schedule_manager.status["error"]:
            st.error(f"❌ 错误：{schedule_manager.status['error']}")

        # 当前定时任务展示卡片
        current_schedule = config.get("schedule", {})
        if current_schedule.get("enabled"):
            st.markdown("---")
            st.markdown("#### 📋 当前定时采集任务")
            
            # 构建任务详情
            school_mode = current_schedule.get("school_mode", "全部学校")
            if school_mode == "全部学校":
                school_info = "全部学校"
            else:
                selected_schools = current_schedule.get("selected_schools", [])
                school_names = [s.get("name", "") for s in selected_schools if s.get("name")]
                if school_names:
                    school_info = f"特定学校：{'、'.join(school_names[:3])}{'...' if len(school_names) > 3 else ''}（共{len(school_names)}所）"
                else:
                    school_info = f"特定学校（{len(selected_schools)} 所）"
            
            depth_info = current_schedule.get("depth", "最近 30 条/校")
            if depth_info == "自定义条数":
                depth_info = f"自定义 {current_schedule.get('depth_custom', 100)} 条/校"
            
            tag_info = "、".join(current_schedule.get("tag_filter", ["图文混合"])) or "全部类型"
            
            # 内容长度（字数档位）
            word_tiers = current_schedule.get("word_tier_filter", [])
            tier_display = {"短": "<30字", "中短": "30-50字", "中长": "50-100字", "长": "≥100字"}
            if word_tiers:
                tier_info = "、".join([f"{t}({tier_display.get(t, t)})" for t in word_tiers])
            else:
                tier_info = "不限"
            
            freq = current_schedule.get("frequency", "每天")
            time_str = current_schedule.get("time", "20:00")
            schedule_time_info = f"{freq} {time_str}"
            
            # 计算下次执行时间
            now = datetime.now()
            h, m = map(int, time_str.split(":"))
            if freq == "每天":
                next_run = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if next_run <= now:
                    next_run += timedelta(days=1)
            elif freq == "每周":
                next_run = now.replace(hour=h, minute=m, second=0, microsecond=0)
                days_ahead = 7 - now.weekday()
                if days_ahead == 0:
                    days_ahead = 7
                next_run += timedelta(days=days_ahead)
            else:
                next_run = now.replace(day=1, hour=h, minute=m, second=0, microsecond=0)
                if next_run <= now:
                    next_run += timedelta(days=30)
            
            # 展示卡片
            card_col1, card_col2 = st.columns([4, 1])
            with card_col1:
                st.markdown(f"""
<div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; border-radius: 10px; color: white; margin-bottom: 10px;">
<div style="display: flex; justify-content: space-between; align-items: center;">
<div>
<div style="font-size: 18px; font-weight: bold; margin-bottom: 8px;"> {schedule_time_info}</div>
<div style="font-size: 14px; opacity: 0.9;">📍 采集范围：{school_info}</div>
<div style="font-size: 14px; opacity: 0.9;">📊 采集深度：{depth_info}</div>
<div style="font-size: 14px; opacity: 0.9;">🏷️ 素材类型：{tag_info}</div>
<div style="font-size: 14px; opacity: 0.9;">📝 内容长度：{tier_info}</div>
<div style="font-size: 13px; opacity: 0.7; margin-top: 6px;">🔜 下次执行：{next_run.strftime('%Y-%m-%d %H:%M')}</div>
</div>
</div>
</div>
""", unsafe_allow_html=True)
            
            with card_col2:
                # 二次确认逻辑
                if st.session_state.get("cancel_schedule_confirm"):
                    st.markdown("**确认取消定时任务？**")
                    col_yes, col_no = st.columns(2)
                    with col_yes:
                        if st.button("✅ 确认", use_container_width=True, key="cancel_schedule_yes"):
                            config["schedule"] = {"enabled": False}
                            save_config(config)
                            st.session_state.cancel_schedule_confirm = False
                            st.success("定时任务已取消")
                            st.rerun()
                    with col_no:
                        if st.button("❌ 不取消", use_container_width=True, key="cancel_schedule_no"):
                            st.session_state.cancel_schedule_confirm = False
                            st.rerun()
                else:
                    if st.button("❌ 取消", use_container_width=True, key="cancel_schedule"):
                        st.session_state.cancel_schedule_confirm = True
                        st.rerun()
        
        st.markdown("---")

        # 定时任务配置
        schedule_config = config.get("schedule", {})
        schedule_enabled = st.checkbox("启用定时采集", value=schedule_config.get("enabled", False))
        
        if schedule_enabled:
            st.markdown("---")
            st.markdown("**采集范围设置**（独立于手动采集）")
            
            # 获取学校列表（与手动采集板块保持一致）
            schools = get_schools_cache()
            if not schools:
                with st.spinner("正在获取学校列表..."):
                    client = AdreamboxClient()
                    schools = client.get_all_schools()
                    save_schools_cache(schools)
            
            school_options = {f"{s['name']} (SID: {s['sid']})": s for s in schools}
            
            # 学校选择
            school_mode = st.radio(
                "学校选择",
                ["全部学校", "特定学校"],
                horizontal=True,
                index=0 if schedule_config.get("school_mode", "全部学校") == "全部学校" else 1,
                key="schedule_school_mode"
            )
            
            selected_schools = []
            if school_mode == "特定学校" and school_options:
                # 学校搜索
                school_search = st.text_input(
                    "搜索学校",
                    placeholder="输入学校名称关键词...",
                    help="输入关键词过滤学校列表",
                    key="schedule_school_search"
                )
                
                # 根据搜索关键词过滤学校列表（支持名称和 ID 搜索）
                if school_search:
                    filtered_options = {k: v for k, v in school_options.items() 
                                       if school_search.lower() in k.lower() 
                                       or school_search in str(v.get('sid', ''))}
                else:
                    filtered_options = school_options
                
                saved_schools = schedule_config.get("selected_schools", [])
                
                # 保持已选学校（使用完整格式）
                if 'schedule_selected_schools' not in st.session_state:
                    # 将保存的学校名称转换为完整格式
                    st.session_state.schedule_selected_schools = []
                    for s in saved_schools:
                        full_name = f"{s['name']} (SID: {s['sid']})"
                        if full_name in school_options:
                            st.session_state.schedule_selected_schools.append(full_name)
                
                # 已选学校始终在选项中（即使不在搜索结果中）
                display_options = dict(filtered_options)
                for k in st.session_state.schedule_selected_schools:
                    if k not in display_options and k in school_options:
                        display_options[k] = school_options[k]
                
                # 获取 multiselect 当前值（如果存在）
                current_widget_value = st.session_state.get("schedule_school_select", [])
                
                selected_names = st.multiselect(
                    "选择学校（可多选）",
                    options=list(display_options.keys()),
                    default=current_widget_value if current_widget_value else st.session_state.schedule_selected_schools,
                    help="不选则默认采集全部学校，切换搜索关键词时保留已选学校",
                    key="schedule_school_select"
                )
                
                # 更新 session_state（合并当前值和已选值）
                if selected_names:
                    st.session_state.schedule_selected_schools = list(set(st.session_state.schedule_selected_schools) | set(selected_names))
                else:
                    st.session_state.schedule_selected_schools = []
                
                selected_schools = [school_options[name] for name in st.session_state.schedule_selected_schools if name in school_options]
            elif school_mode == "特定学校" and not school_options:
                st.info("学校列表加载中，请稍后...")
            
            # 采集深度模式选择
            depth_mode = st.radio(
                "采集深度模式",
                ["按条数限制", "按时间范围"],
                horizontal=True,
                key="schedule_depth_mode"
            )
            
            depth = None
            date_start = None
            date_end = None
            
            if depth_mode == "按条数限制":
                depth_options = ["最近 30 条/校", "最近 100 条/校", "最近 300 条/校", "全量采集", "自定义条数"]
                saved_depth = schedule_config.get("depth", "最近 30 条/校")
                depth = st.selectbox(
                    "采集条数",
                    depth_options,
                    index=depth_options.index(saved_depth) if saved_depth in depth_options else 0,
                    key="schedule_depth"
                )
                
                if depth == "自定义条数":
                    depth = st.number_input(
                        "每校采集条数",
                        min_value=1,
                        max_value=1000,
                        value=schedule_config.get("depth_custom", 100),
                        step=10,
                        key="schedule_depth_custom"
                    )
            else:  # 按时间范围
                col_date1, col_date2 = st.columns(2)
                with col_date1:
                    date_start = st.date_input(
                        "开始日期",
                        value=datetime.now() - timedelta(days=30),
                        key="schedule_date_start",
                        help="采集该日期之后的动态"
                    )
                with col_date2:
                    date_end = st.date_input(
                        "结束日期",
                        value=datetime.now(),
                        key="schedule_date_end",
                        help="采集该日期之前的动态"
                    )
            
            # 图片处理策略
            image_strategy = st.radio(
                "图片处理策略",
                ["仅保存图片链接", "下载并转存到飞书"],
                horizontal=True,
                index=0 if schedule_config.get("image_strategy", "仅保存图片链接") == "仅保存图片链接" else 1,
                key="schedule_image_strategy"
            )
            
            # 获取完整内容选项
            fetch_detail = st.checkbox(
                "获取完整内容（访问详情页）",
                value=schedule_config.get("fetch_detail", True),
                help="列表页只显示前 100 字摘要，勾选后会访问每条动态的详情页获取完整内容（采集速度会变慢）",
                key="schedule_fetch_detail"
            )
            
            # 素材类型筛选
            st.markdown("**素材类型筛选**（可交叉组合）")
            col_tag1, col_tag2, col_tag3 = st.columns(3)
            saved_tags = schedule_config.get("tag_filter", [])
            with col_tag1:
                tag_mixed = st.checkbox("图文混合", value="图文混合" in saved_tags if saved_tags else True, key="schedule_tag_mixed")
            with col_tag2:
                tag_image = st.checkbox("纯图片", value="纯图片" in saved_tags if saved_tags else False, key="schedule_tag_image")
            with col_tag3:
                tag_text = st.checkbox("纯文字", value="纯文字" in saved_tags if saved_tags else False, key="schedule_tag_text")
            
            # 图文混合的下一级选项：字数档位
            word_tier_filter = []
            if tag_mixed:
                st.markdown("&nbsp;&nbsp;&nbsp;&nbsp;**内容长度（仅图文混合）：**")
                col_t1, col_t2, col_t3, col_t4 = st.columns(4)
                saved_tiers = schedule_config.get("word_tier_filter", ["短", "中短", "中长", "长"])
                with col_t1:
                    tier_short = st.checkbox("短(<30字)", value="短" in saved_tiers, key="schedule_tier_short")
                with col_t2:
                    tier_mid_short = st.checkbox("中短(30-50字)", value="中短" in saved_tiers, key="schedule_tier_mid_short")
                with col_t3:
                    tier_mid_long = st.checkbox("中长(50-100字)", value="中长" in saved_tiers, key="schedule_tier_mid_long")
                with col_t4:
                    tier_long = st.checkbox("长(≥100字)", value="长" in saved_tiers, key="schedule_tier_long")
                
                if tier_short:
                    word_tier_filter.append("短")
                if tier_mid_short:
                    word_tier_filter.append("中短")
                if tier_mid_long:
                    word_tier_filter.append("中长")
                if tier_long:
                    word_tier_filter.append("长")
            
            st.markdown("---")
            st.markdown("**调度设置**")
            
            col1, col2, col3 = st.columns(3)
            with col1:
                frequency = st.selectbox(
                    "频率", 
                    ["每天", "每周", "每月"],
                    index=["每天", "每周", "每月"].index(schedule_config.get("frequency", "每天"))
                )
            with col2:
                time_val = st.time_input(
                    "时间", 
                    value=datetime.strptime(schedule_config.get("time", "20:00"), "%H:%M")
                )
            with col3:
                # 计算下次执行时间
                now = datetime.now()
                target_time = datetime.strptime(schedule_config.get("time", "20:00"), "%H:%M")
                if frequency == "每天":
                    next_run = now.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)
                    if next_run <= now:
                        next_run += timedelta(days=1)
                elif frequency == "每周":
                    next_run = now.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)
                    days_ahead = 7 - now.weekday()
                    if days_ahead == 0:
                        days_ahead = 7
                    next_run += timedelta(days=days_ahead)
                else:
                    next_run = now.replace(day=1, hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)
                    if next_run <= now:
                        next_run += timedelta(days=30)
                
                st.metric("下次执行", next_run.strftime("%m-%d %H:%M"))
            
            # 保存定时任务配置
            if st.button(" 保存定时任务配置", use_container_width=True):
                tag_filter = []
                if tag_mixed:
                    tag_filter.append("图文混合")
                if tag_image:
                    tag_filter.append("纯图片")
                if tag_text:
                    tag_filter.append("纯文字")
                
                config["schedule"] = {
                    "enabled": True,
                    "school_mode": school_mode,
                    "selected_schools": selected_schools,
                    "depth": depth,
                    "image_strategy": image_strategy,
                    "tag_filter": tag_filter,
                    "word_tier_filter": word_tier_filter,
                    "frequency": frequency,
                    "time": time_val.strftime("%H:%M"),
                }
                save_config(config)
                st.success("定时任务配置已保存！")
                st.rerun()
            
            # 立即执行一次
            if st.button("▶ 立即执行一次", use_container_width=True, type="secondary"):
                if schedule_manager.status["is_running"]:
                    st.warning("定时采集正在执行中，请等待完成")
                else:
                    # 保存当前配置
                    tag_filter = []
                    if tag_mixed:
                        tag_filter.append("图文混合")
                    if tag_image:
                        tag_filter.append("纯图片")
                    if tag_text:
                        tag_filter.append("纯文字")
                    
                    config["schedule"] = {
                        "enabled": True,
                        "school_mode": school_mode,
                        "selected_schools": selected_schools,
                        "depth": depth,
                        "image_strategy": image_strategy,
                        "fetch_detail": fetch_detail,
                        "tag_filter": tag_filter,
                        "word_tier_filter": word_tier_filter,
                        "frequency": frequency,
                        "time": time_val.strftime("%H:%M"),
                    }
                    save_config(config)
                    
                    # 立即执行
                    st.info("正在启动定时采集任务...")
                    schedule_manager._run_schedule_task(config, config["schedule"])
                    st.success(f"✅ 定时采集已执行！{schedule_manager.status['message']}")
                    st.rerun()
            
            st.warning("⚠️ **定时任务执行说明**：Streamlit Cloud 免费版应用 30 分钟无交互会自动休眠，定时任务无法在休眠状态下执行。如需定时采集准时运行，请使用 Streamlit Pro/Team 版或在本地/自有服务器运行。")
        else:
            if st.button("💾 保存定时任务配置", use_container_width=True):
                config["schedule"] = {"enabled": False}
                save_config(config)
                st.success("定时任务已禁用")

    with tab2:
        st.subheader("数据概览")

        # 从飞书读取统计数据
        try:
            feishu_cfg = config.get("feishu", {})
            writer = FeishuWriter(
                app_id=feishu_cfg.get("app_id", ""),
                app_secret=feishu_cfg.get("app_secret", ""),
                app_token=feishu_cfg.get("app_token", ""),
                table_id=feishu_cfg.get("table_id", ""),
            )
            records = writer.read_all_records() or []
            
            total = len(records)
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            
            # 统计总图文混合素材
            total_image_text = 0
            for record in records:
                fields = record.get("fields", {})
                if fields.get("素材类型", "") == "图文混合":
                    total_image_text += 1
            
            # 左右分栏布局
            left_col, right_col = st.columns(2)
            
            with left_col:
                st.markdown("#### 总采集动态")
                st.metric("采集总数", total)
                st.metric("图文混合素材", total_image_text)
                st.caption("数据来源：飞书多维表格")
            
            with right_col:
                st.markdown("#### 时间范围筛选")
                time_filter = st.radio("", ["今日", "本周", "本月"], horizontal=True, label_visibility="collapsed")
                
                # 计算时间范围
                if time_filter == "今日":
                    start_date = today
                elif time_filter == "本周":
                    # 本周一
                    start_date = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
                else:  # 本月
                    # 本月1号
                    start_date = now.replace(day=1).strftime("%Y-%m-%d")
                
                period_count = 0
                period_image_text = 0
                schools_set = set()
                
                for record in records:
                    fields = record.get("fields", {})
                    # 时间筛选（采集时间是毫秒时间戳，需要转换）
                    collect_time = fields.get("采集时间", "")
                    if collect_time:
                        try:
                            # 飞书返回的是毫秒时间戳
                            ts = int(collect_time) / 1000
                            record_date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                            if record_date >= start_date:
                                period_count += 1
                                # 图文混合素材
                                if fields.get("素材类型", "") == "图文混合":
                                    period_image_text += 1
                                # 覆盖学校
                                school_name = fields.get("来源学校", "")
                                if school_name:
                                    schools_set.add(school_name)
                        except (ValueError, TypeError, OSError):
                            pass
                
                st.metric(f"{time_filter}采集总量", period_count)
                st.metric(f"{time_filter}图文混合素材", period_image_text)
                st.metric(f"{time_filter}覆盖学校", len(schools_set))
                st.caption("数据来源：飞书多维表格")
            
            if total == 0:
                st.info(" 表格暂无数据，请先在「采集控制」页面开始采集")
        except Exception as e:
            st.error(f"读取飞书数据失败：{e}")
            left_col, right_col = st.columns(2)
            with left_col:
                st.markdown("#### 总采集动态")
                st.metric("采集总数", 0)
                st.metric("图文混合素材", 0)
            with right_col:
                st.markdown("#### 时间范围筛选")
                st.metric("今日采集总量", 0)
                st.metric("今日图文混合素材", 0)
                st.metric("今日覆盖学校", 0)

        st.markdown("---")
        st.subheader("素材类型分布")
        
        # 从飞书读取统计数据
        try:
            feishu_cfg = config.get("feishu", {})
            writer = FeishuWriter(
                app_id=feishu_cfg.get("app_id", ""),
                app_secret=feishu_cfg.get("app_secret", ""),
                app_token=feishu_cfg.get("app_token", ""),
                table_id=feishu_cfg.get("table_id", ""),
            )
            records = writer.read_all_records() or []
            
            # 统计素材类型
            type_counts = {"图文混合": 0, "纯图片": 0, "纯文字": 0}
            # 统计图文混合的字数档位
            word_tier_counts = {"短": 0, "中短": 0, "中长": 0, "长": 0}
            
            for record in records:
                fields = record.get("fields", {})
                material_type = fields.get("素材类型", "纯文字")
                if material_type in type_counts:
                    type_counts[material_type] += 1
                
                # 统计图文混合的字数档位
                if material_type == "图文混合":
                    word_tier = fields.get("图文字数档位", "")
                    if word_tier in word_tier_counts:
                        word_tier_counts[word_tier] += 1
            
            total = sum(type_counts.values())
            
            if total > 0:
                # 显示素材类型饼图
                import plotly.graph_objects as go
                from plotly.subplots import make_subplots
                
                fig = make_subplots(rows=1, cols=2, specs=[[{'type':'domain'}, {'type':'domain'}]])
                
                # 素材类型分布
                fig.add_trace(go.Pie(
                    labels=list(type_counts.keys()),
                    values=list(type_counts.values()),
                    hole=0.4,
                    marker_colors=["#FF6B6B", "#4ECDC4", "#45B7D1"],
                    name="素材类型"
                ), 1, 1)
                
                # 图文混合字数档位分布
                fig.add_trace(go.Pie(
                    labels=list(word_tier_counts.keys()),
                    values=list(word_tier_counts.values()),
                    hole=0.4,
                    marker_colors=["#95E1D3", "#F38181", "#AA96DA", "#FCBAD3"],
                    name="字数档位"
                ), 1, 2)
                
                fig.update_layout(
                    title="素材类型分布",
                    showlegend=True,
                    height=400
                )
                st.plotly_chart(fig, use_container_width=True)
                
                # 显示详细数据
                col_type1, col_type2, col_type3 = st.columns(3)
                col_type1.metric("图文混合", f"{type_counts['图文混合']} ({type_counts['图文混合']/total*100:.1f}%)")
                col_type2.metric("纯图片", f"{type_counts['纯图片']} ({type_counts['纯图片']/total*100:.1f}%)")
                col_type3.metric("纯文字", f"{type_counts['纯文字']} ({type_counts['纯文字']/total*100:.1f}%)")
                
                # 显示字数档位详细数据
                st.markdown("**图文混合字数档位分布：**")
                col_tier1, col_tier2, col_tier3, col_tier4 = st.columns(4)
                image_text_total = type_counts['图文混合']
                if image_text_total > 0:
                    col_tier1.metric("短(<30字)", f"{word_tier_counts['短']} ({word_tier_counts['短']/image_text_total*100:.1f}%)")
                    col_tier2.metric("中短(30-50字)", f"{word_tier_counts['中短']} ({word_tier_counts['中短']/image_text_total*100:.1f}%)")
                    col_tier3.metric("中长(50-100字)", f"{word_tier_counts['中长']} ({word_tier_counts['中长']/image_text_total*100:.1f}%)")
                    col_tier4.metric("长(≥100字)", f"{word_tier_counts['长']} ({word_tier_counts['长']/image_text_total*100:.1f}%)")
            else:
                st.info("暂无数据，请先完成采集")
        except Exception as e:
            st.info(f"暂无数据或读取失败：{e}")

    with tab3:
        st.subheader("采集历史记录")
        
        init_crawl_log()
        logs = get_crawl_logs()
        
        # 创建删除确认弹窗
        delete_modal = Modal("确认删除", key="delete_modal")
        
        if logs:
            # 检查是否有删除操作
            if 'delete_log_id' in st.session_state and 'delete_confirmed' in st.session_state:
                log_id_to_delete = st.session_state.delete_log_id
                if st.session_state.delete_confirmed:
                    # 获取要删除的记录信息
                    log_to_delete = next((log for log in logs if log['id'] == log_id_to_delete), None)
                    if log_to_delete:
                        log_time = log_to_delete['timestamp'][:19]
                        log_mode = log_to_delete['school_mode']
                        log_new = log_to_delete['new_count']
                        
                        try:
                            conn = sqlite3.connect(DB_PATH)
                            cursor = conn.cursor()
                            cursor.execute('DELETE FROM crawl_log WHERE id = ?', (log_id_to_delete,))
                            conn.commit()
                            conn.close()
                            st.success(f"✅ 已删除采集记录：{log_time} - {log_mode} ({log_new} 新增)")
                        except Exception as e:
                            st.error(f"删除失败：{e}")
                
                # 清理状态
                del st.session_state.delete_log_id
                del st.session_state.delete_confirmed
                st.rerun()
            
            for log in logs:
                with st.expander(f"{log['timestamp'][:19]} - {log['school_mode']} ({log['new_count']} 新增)"):
                    # 删除按钮
                    col_del1, col_del2 = st.columns([4, 1])
                    with col_del2:
                        if st.button("🗑️ 删除此记录", key=f"delete_{log['id']}", use_container_width=True):
                            st.session_state.delete_log_id = log['id']
                            delete_modal.open()
                    
                    # 显示统计信息
                    st.write(f"**学校数量：** {log['school_count']}")
                    st.write(f"**新增：** {log['new_count']} 条")
                    st.write(f"**跳过：** {log['skip_count']} 条")
                    st.write(f"**学校失败：** {log['error_count']} 所")
                    if log.get('feed_error_details'):
                        import json
                        feed_errors = json.loads(log['feed_error_details'])
                        st.write(f"**动态失败：** {len(feed_errors)} 条")
                    if log.get('feed_warning_details'):
                        import json
                        feed_warnings = json.loads(log['feed_warning_details'])
                        st.write(f"**动态警告：** {len(feed_warnings)} 条（详情页获取失败，仅保留摘要）")
                    st.write(f"**耗时：** {log['duration_seconds']:.1f} 秒")
                    
                    # 显示统计说明
                    if log.get('summary_msg'):
                        st.info(log['summary_msg'])
                    
                    # 显示跳过详情
                    if log.get('skip_count', 0) > 0 and log.get('skipped_details'):
                        import json
                        skipped = json.loads(log['skipped_details'])
                        st.markdown(f"** 跳过详情（{len(skipped)}条）：**")
                        for item in skipped:
                            reason = item.get("reason", "已采集过")
                            collected_at = item.get("collected_at", "未知")
                            st.write(f"- **动态 ID:** {item['feed_id']} | **学校:** {item['school_name']} | **上次采集:** {collected_at} | **原因:** {reason}")
                    
                    # 显示每所学校处理结果
                    if log.get('school_details'):
                        import json
                        school_details = json.loads(log['school_details'])
                        st.info(f"**每所学校处理结果（{len(school_details)}所）：**")
                        for school in school_details:
                            name = school.get('name', '')
                            status = school.get('status', '')
                            collected = school.get('collected', 0)
                            fetched = school.get('fetched', 0)
                            filtered = school.get('filtered', 0)
                            reason = school.get('reason', '')
                            
                            if status == 'success':
                                status_icon = "✅"
                                status_text = f"成功（新增{collected}条，处理{fetched}条"
                                if filtered > 0:
                                    status_text += f"，过滤{filtered}条"
                                status_text += "）"
                            elif status == 'error':
                                status_icon = "❌"
                                status_text = f"失败：{reason}"
                            else:
                                status_icon = "⚠️"
                                status_text = f"无动态：{reason}"
                            
                            st.write(f"{status_icon} **{name}** - {status_text}")
            
            # 显示删除确认弹窗
            if delete_modal.is_open():
                with delete_modal.container():
                    log_id_to_delete = st.session_state.get('delete_log_id')
                    log_to_delete = next((log for log in logs if log['id'] == log_id_to_delete), None)
                    
                    if log_to_delete:
                        log_time = log_to_delete['timestamp'][:19]
                        log_mode = log_to_delete['school_mode']
                        log_new = log_to_delete['new_count']
                        
                        st.warning("确认删除以下采集记录？")
                        st.info(f"时间：{log_time}\n模式：{log_mode}\n新增：{log_new} 条")
                        
                        col_confirm1, col_confirm2 = st.columns(2)
                        with col_confirm1:
                            if st.button("✅ 确认删除", key="confirm_delete_yes", use_container_width=True, type="primary"):
                                st.session_state.delete_confirmed = True
                                delete_modal.close()
                                st.rerun()
                        with col_confirm2:
                            if st.button("❌ 取消删除", key="confirm_delete_no", use_container_width=True):
                                st.session_state.delete_confirmed = False
                                delete_modal.close()
                                st.rerun()
                    
        else:
            st.info("暂无采集记录")

    with tab4:
        st.subheader("飞书配置")

        feishu_cfg = config.get("feishu", {})

        col1, col2 = st.columns(2)
        with col1:
            app_id = st.text_input("App ID", value=feishu_cfg.get("app_id", ""))
            app_secret = st.text_input(
                "App Secret", value=feishu_cfg.get("app_secret", ""), type="password"
            )
        with col2:
            app_token = st.text_input("App Token", value=feishu_cfg.get("app_token", ""))
            table_id = st.text_input("Table ID", value=feishu_cfg.get("table_id", ""))

        if st.button(" 保存飞书配置"):
            config["feishu"] = {
                "app_id": app_id,
                "app_secret": app_secret,
                "app_token": app_token,
                "table_id": table_id,
            }
            save_config(config)
            
            # 同步更新 session_state，让采集功能使用界面输入的凭证
            st.session_state.app_id = app_id
            st.session_state.app_secret = app_secret
            st.session_state.app_token = app_token
            st.session_state.table_id = table_id
            st.session_state.config_table_id = st.session_state.get("config_table_id", "")
            
            # 重置全局配置管理器，让它重新读取凭证
            global _config_manager
            _config_manager = None
            
            st.success("✅ 飞书配置已保存！")

        # 测试连接按钮
        if st.button(" 测试连接", key="test_feishu_connection"):
            if not app_id or not app_secret:
                st.error("请先填写 App ID 和 App Secret")
            else:
                with st.spinner("正在测试飞书连接..."):
                    try:
                        writer = FeishuWriter(app_id, app_secret, app_token or "", table_id or "")
                        
                        # 测试获取 token
                        token = writer._get_token()
                        if token:
                            st.success("✅ 飞书应用连接成功！")
                            
                            # 测试表格访问
                            if app_token and table_id:
                                try:
                                    records = writer.read_records(page_size=1)
                                    st.success(f"✅ 表格访问成功！当前表格有 {len(records)} 条记录（仅测试读取）")
                                    
                                    # 测试写入权限（写入测试记录后立即删除）
                                    with st.spinner("正在测试写入权限..."):
                                        try:
                                            # 直接使用原始 API 写入，只用最基础的字段
                                            token = writer._get_token()
                                            url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
                                            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                                            test_payload = {
                                                "fields": {
                                                    "文本": "【测试记录】正在测试写入权限，此记录将自动删除",
                                                }
                                            }
                                            resp = requests.post(url, headers=headers, json=test_payload, timeout=10)
                                            if resp.status_code != 200:
                                                raise Exception(f"写入失败：{resp.json()}")
                                            result = resp.json()
                                            test_record_id = None
                                            if isinstance(result, dict) and result.get("code") == 0:
                                                data = result.get("data", {})
                                                if isinstance(data, dict):
                                                    record = data.get("record", {})
                                                    if isinstance(record, dict):
                                                        test_record_id = record.get("record_id")
                                            
                                            # 删除测试记录
                                            if test_record_id:
                                                del_url = f"{url}/{test_record_id}"
                                                requests.delete(del_url, headers=headers, timeout=10)
                                                st.success("✅ 写入权限测试成功！测试记录已自动删除")
                                            else:
                                                st.warning("⚠️ 写入成功但未返回记录 ID")
                                        except Exception as write_err:
                                            st.warning(f"⚠️ 读取权限正常，但写入权限不足：{write_err}")
                                            st.info("请检查飞书应用是否有表格的编辑权限（需要在表格中邀请应用为协作者）")
                                except Exception as e:
                                    st.warning(f"⚠️ 应用连接成功，但表格访问失败：{e}")
                                    st.info("请检查 App Token 和 Table ID 是否正确，以及应用是否有表格访问权限")
                        else:
                            st.error("❌ 获取访问令牌失败，请检查 App ID 和 App Secret")
                    except Exception as e:
                        error_msg = str(e)
                        if "invalid app" in error_msg.lower():
                            st.error("❌ App ID 或 App Secret 无效，请检查是否正确")
                        elif "permission" in error_msg.lower() or "scope" in error_msg.lower():
                            st.error(f"❌ 权限不足：{error_msg}")
                            st.info("请在飞书开放平台为应用添加所需权限并重新发布版本")
                        else:
                            st.error(f"❌ 连接失败：{error_msg}")

        st.markdown("---")
        st.subheader("采集配置")

        crawl_cfg = config.get("crawl", {})

        col1, col2, col3 = st.columns(3)
        with col1:
            request_interval = st.number_input(
                "请求间隔（秒）",
                value=crawl_cfg.get("request_interval", 0.5),
                min_value=0.1,
                max_value=5.0,
                step=0.1,
            )
        with col2:
            timeout = st.number_input(
                "超时时间（秒）",
                value=crawl_cfg.get("timeout", 30),
                min_value=5,
                max_value=120,
                step=5,
            )
        with col3:
            retry_count = st.number_input(
                "重试次数",
                value=crawl_cfg.get("retry_count", 3),
                min_value=0,
                max_value=10,
                step=1,
            )

        max_per_school = st.number_input(
            "每校最大条数（0=不限）",
            value=crawl_cfg.get("max_per_school", 0),
            min_value=0,
            step=10,
        )

        if st.button(" 保存采集配置"):
            config["crawl"] = {
                "request_interval": request_interval,
                "timeout": timeout,
                "retry_count": retry_count,
                "max_per_school": max_per_school,
            }
            save_config(config)
            st.success("采集配置已保存！")

        st.subheader("存储管理")
        
        # 显示存储使用情况
        def get_storage_usage():
            """获取存储使用情况"""
            usage = {}
            
            # 学校缓存
            cache_file = "data/schools_cache.json"
            if os.path.exists(cache_file):
                usage["学校缓存"] = os.path.getsize(cache_file)
            else:
                usage["学校缓存"] = 0
            
            # 去重数据库
            if os.path.exists(DB_PATH):
                usage["去重数据库"] = os.path.getsize(DB_PATH)
            else:
                usage["去重数据库"] = 0
            
            # 采集日志（crawl_log 表在 collected.db 中，单独计算表大小）
            if os.path.exists(DB_PATH):
                # 获取 crawl_log 表的近似大小
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM crawl_log")
                log_count = cursor.fetchone()[0]
                conn.close()
                # 每条日志约 1KB，估算大小
                usage["采集日志"] = log_count * 1024
            else:
                usage["采集日志"] = 0
            
            # 配置文件
            if os.path.exists(CONFIG_PATH):
                usage["配置文件"] = os.path.getsize(CONFIG_PATH)
            else:
                usage["配置文件"] = 0
            
            return usage
        
        def format_size(size_bytes):
            """格式化文件大小"""
            if size_bytes < 1024:
                return f"{size_bytes} B"
            elif size_bytes < 1024 * 1024:
                return f"{size_bytes / 1024:.1f} KB"
            elif size_bytes < 1024 * 1024 * 1024:
                return f"{size_bytes / (1024 * 1024):.1f} MB"
            else:
                return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"
        
        usage = get_storage_usage()
        total_size = sum(usage.values())
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("学校缓存", format_size(usage["学校缓存"]))
        col2.metric("去重数据库", format_size(usage["去重数据库"]))
        col3.metric("采集日志", format_size(usage["采集日志"]))
        col4.metric("总计", format_size(total_size))
        
        st.progress(min(total_size / (1024 * 1024 * 1024), 1.0))
        st.caption(f"Streamlit Cloud 免费限制：1GB | 已使用：{format_size(total_size)} ({total_size / (1024 * 1024 * 1024) * 100:.2f}%)")
        
        # 缓存清理
        st.markdown("**清理缓存：**")
        
        # 清理学校缓存
        st.markdown("**清理学校缓存**")
        st.caption("删除本地缓存的学校名称映射文件，下次采集时自动重新生成。不影响已采集数据。")
        if st.button("️ 清理学校缓存", use_container_width=True):
            if os.path.exists("data/schools_cache.json"):
                os.remove("data/schools_cache.json")
                st.toast("学校缓存已清理", icon="✅")
                st.rerun()
            else:
                st.toast("学校缓存不存在，无需清理", icon="ℹ️")
        
        st.markdown("---")
        
        # 清理采集日志
        st.markdown("**清理采集日志**")
        st.caption("清空本地采集历史记录，删除后无法恢复。不影响已采集数据（数据存在飞书多维表格中）。")
        clear_log_confirm = st.text_input('请输入"清理"以确认清理采集日志', key="clear_log_confirm")
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("🗑️ 确认清理采集日志", use_container_width=True, disabled=(clear_log_confirm != "清理")):
                try:
                    conn = sqlite3.connect(DB_PATH)
                    cursor = conn.cursor()
                    cursor.execute('DELETE FROM crawl_log')
                    conn.commit()
                    conn.close()
                    st.toast("采集日志已清理，历史记录已删除", icon="✅")
                    st.rerun()
                except Exception as e:
                    st.toast(f"清理失败：{str(e)}", icon="❌")
        
        st.markdown("---")
        
        # 重置去重记录
        st.markdown("**⚠️ 重置去重记录**")
        st.caption("""
        去重数据库记录已采集的动态 ID，防止重复采集。
        重置后，下次采集会将所有动态视为新数据，可能导致飞书表格中出现重复记录。
        
        建议场景：
        - 需要重新采集所有历史数据时
        - 去重数据库损坏时
        """)
        
        # 显示当前去重记录数和数据库大小
        dedup = DedupManager()
        record_count = dedup.get_count()
        db_size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
        st.caption(f"当前去重记录数：{record_count} 条 | 数据库大小：{format_size(db_size)}")
        
        reset_confirm = st.text_input('请输入"重置"以确认重置去重记录', key="reset_dedup_confirm")
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("⚠️ 确认重置去重记录", use_container_width=True, disabled=(reset_confirm != "重置")):
                dedup.reset()
                st.toast(f"去重记录已重置（原 {record_count} 条记录已清除）", icon="✅")
                st.rerun()
        
        st.markdown("---")
        
        st.subheader("访问密码")
        
        # 从 config.json 读取密码配置（兼容 Streamlit Cloud 只读文件系统）
        auth_cfg = config.get("auth", {})
        auth_enabled = auth_cfg.get("enabled", False)
        
        # 启用/禁用密码开关
        enable_auth = st.toggle(
            "启用访问密码",
            value=auth_enabled,
            help="启用后，访问页面时需要输入密码"
        )
        
        # 如果开关状态改变，更新配置
        if enable_auth != auth_enabled:
            config["auth"] = {
                "enabled": enable_auth,
                "password": auth_cfg.get("password", "")
            }
            save_config(config)
            st.success(f"✅ 访问密码已{'启用' if enable_auth else '禁用'}")
            st.rerun()
        
        if enable_auth:
            st.markdown("**设置访问密码**")
            st.caption("设置后需重新登录，密码会保存到配置文件中")
            
            current_pwd = auth_cfg.get("password", "")
            has_password = bool(current_pwd)
            
            if has_password:
                old_pwd = st.text_input("当前密码", type="password", key="change_old_pwd")
            
            new_pwd = st.text_input("新密码", type="password", key="change_new_pwd")
            confirm_pwd = st.text_input("确认新密码", type="password", key="change_confirm_pwd")
            
            col1, col2 = st.columns([1, 3])
            with col1:
                if st.button("修改密码", type="primary"):
                    if not new_pwd or not confirm_pwd:
                        st.error("请填写密码字段")
                    elif has_password and not old_pwd:
                        st.error("请填写当前密码")
                    elif has_password and old_pwd != current_pwd:
                        st.error("当前密码错误")
                    elif new_pwd != confirm_pwd:
                        st.error("两次输入的新密码不一致")
                    elif len(new_pwd) < 6:
                        st.error("密码长度不能少于 6 位")
                    else:
                        config["auth"] = {
                            "enabled": True,
                            "password": new_pwd
                        }
                        save_config(config)
                        st.session_state["authenticated"] = False
                        st.toast("密码已修改，请重新登录", icon="✅")
                        st.rerun()
        else:
            st.info("访问密码已禁用，任何人都可以访问页面")
        
        st.markdown("---")
        
        st.info("💡 提示：Streamlit Cloud 免费版有 1GB 存储限制。定期清理缓存可以避免超限。学校缓存和采集日志可以安全清理，不影响已采集的数据（数据存在飞书多维表格中）。")


if __name__ == "__main__":
    main()
