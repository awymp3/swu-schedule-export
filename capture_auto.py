#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
西南大学课程表自动抓取器
=================================================
反检测登录方案：undetected_chromedriver + ddddocr 验证码识别

流程（全自动）：
  1. 首次运行输入西南大学统一身份认证账号和密码（可勾选记住，本地保存到 .env）
  2. 自动检测系统/Chrome版本，下载匹配的 chromedriver（淘宝镜像）
  3. 启动反检测 Chrome，自动登录（OCR 验证码，失败自动重试）
  4. 跳转西南大学个人课表页，选择学年/学期，抓取完整表格课表 DOM → captured.js
  5. 打开可视化应用 index.html（自动载入课表）

支持: macOS (Apple/Intel) · Windows · Linux
用法:  python3 capture_auto.py
"""

import os
import sys
import ssl
import time
import json
import re
import zipfile
import base64
import subprocess
import platform
import webbrowser
import urllib.request
import threading
from html import unescape

# 关闭 SSL 证书校验（避免本机缺少 CA 证书导致请求失败）
ssl._create_default_https_context = ssl._create_unverified_context

# Pillow 兼容补丁：新版 Pillow(10+) 移除了 Image.ANTIALIAS，而 ddddocr 仍在用它
# 必须在 import ddddocr 之前执行
from PIL import Image as _PILImage
if not hasattr(_PILImage, "ANTIALIAS"):
    _PILImage.ANTIALIAS = _PILImage.LANCZOS

# Python 3.12 移除了标准库 distutils，但 undetected-chromedriver 仍会导入
# distutils.version。setuptools 提供兼容实现；这里在导入第三方库前注册它。
try:
    import distutils.version  # Python 3.11 及更早版本
except ModuleNotFoundError:
    try:
        import setuptools._distutils as _setuptools_distutils
        import setuptools._distutils.version as _setuptools_distutils_version
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "缺少 setuptools，无法为 Python 3.12+ 提供 distutils 兼容层。"
        ) from exc
    sys.modules.setdefault("distutils", _setuptools_distutils)
    sys.modules.setdefault("distutils.version", _setuptools_distutils_version)

import undetected_chromedriver as uc
import ddddocr
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ================= 配置 =================
HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_HTML = os.path.join(HERE, "index.html")
CAPTURED_JS = os.path.join(HERE, "captured.js")
ENV_FILE = os.path.join(HERE, ".env")                # 本地保存统一身份认证信息（首次输入后）
LEGACY_ACCOUNT_FILE = os.path.join(HERE, "account.json")
DRIVER_DIR = os.path.join(HERE, "drivers")          # 各平台 chromedriver 缓存目录
BROWSER_DIR = os.path.join(HERE, "browsers")         # 未安装 Chrome 时的便携浏览器缓存目录
CFT_MIRROR = "https://registry.npmmirror.com/-/binary/chrome-for-testing"
CFT_OFFICIAL = "https://storage.googleapis.com/chrome-for-testing-public"
# 当镜像目录短暂不可访问时仍可尝试该已验证版本；目录恢复后优先使用最新版本。
CFT_FALLBACK_VERSION = "152.0.7951.0"

# [代理] 自动探测：用真实 HTTP 请求验证代理是否可用，否则直连
PROXY = "127.0.0.1:7897"

# [网址]（默认西南大学；其他学校改这两个网址即可）
URL_START = "https://jw.swu.edu.cn/sso/zllogin"
URL_KEBIAO = "https://jw.swu.edu.cn/jwglxt/kbcx/xskbcx_cxXskbcxIndex.html?gnmkdm=N2151&layout=default"

# [登录页元素 ID]（正方通用）
ID_USER = "loginName"
ID_PWD = "password"
ID_CODE = "validateCode"
ID_IMG = "kaptchaImage"
ID_TISHI = "tishi"

# 运行时填充的账号（来自 .env 或弹窗输入）
USERNAME = ""
PASSWORD = ""


def _proxy_works(hostport):
    proxy = f"http://{hostport}"
    handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    opener = urllib.request.build_opener(handler)
    try:
        req = urllib.request.Request(URL_START, headers={"User-Agent": "Mozilla/5.0"})
        opener.open(req, timeout=4)
        return True
    except Exception:
        return False


def setup_proxy():
    """探测并设置代理；本地连接不走代理。返回是否启用代理。"""
    for k in ("http_proxy", "https_proxy", "all_proxy",
              "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        os.environ.pop(k, None)
    if _proxy_works(PROXY):
        os.environ["http_proxy"] = f"http://{PROXY}"
        os.environ["https_proxy"] = f"http://{PROXY}"
        os.environ["all_proxy"] = f"socks5://{PROXY}"
        # 本地 chromedriver 通信不走代理，否则 SSL EOF
        os.environ["no_proxy"] = "localhost,127.0.0.1,::1"
        os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
        return True
    return False


# ================= 日志 =================
class C:
    CYAN = '\033[96m'; GREEN = '\033[92m'; YELLOW = '\033[93m'
    RED = '\033[91m'; HEADER = '\033[95m'; BOLD = '\033[1m'; ENDC = '\033[0m'

def log(msg, level="INFO"):
    t = time.strftime('%H:%M:%S')
    color = {"INFO": C.CYAN, "SUCCESS": C.GREEN, "WARN": C.YELLOW,
             "ERROR": C.RED, "SYSTEM": C.HEADER}.get(level, C.ENDC)
    print(f"{C.BOLD}[{t}]{C.ENDC} {color}{msg}{C.ENDC}", flush=True)


def force_kill_chrome():
    log("清理残留 Chrome 进程...", "SYSTEM")
    sysname = platform.system().lower()
    try:
        if "darwin" in sysname or "linux" in sysname:
            os.system("pkill -9 -f 'Google Chrome' 2>/dev/null")
            os.system("pkill -9 -f 'chromedriver' 2>/dev/null")
        elif "windows" in sysname:
            os.system("taskkill /F /IM chrome.exe /T >nul 2>&1")
            os.system("taskkill /F /IM chromedriver.exe /T >nul 2>&1")
    except Exception:
        pass


# ===== 跨平台 chromedriver 准备 =====
IS_MAC = platform.system().lower() == "darwin"
IS_WIN = platform.system().lower() == "windows"


class MacFixPatcher(uc.Patcher):
    """Mac 上 uc 在线 patch 会破坏签名导致 driver 被 -9 杀，patch 后重新签名。"""
    def auto(self, *args, **kwargs):
        result = super().auto(*args, **kwargs)
        if IS_MAC:
            try:
                path = self.executable_path
                os.system(f"xattr -c '{path}' 2>/dev/null")
                os.chmod(path, 0o755)
                subprocess.run(f"codesign --force --deep --sign - '{path}'",
                               shell=True, capture_output=True)
            except Exception:
                pass
        return result
uc.Patcher = MacFixPatcher


def _chrome_major(executable):
    """读取 Chrome 可执行文件版本，失败返回 None。"""
    if not executable or (os.path.sep in executable and not os.path.exists(executable)):
        return None
    try:
        out = subprocess.check_output([executable, "--version"],
                                      stderr=subprocess.DEVNULL).decode(errors="ignore")
        m = re.search(r"(\d+)\.\d+\.\d+", out)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def find_chrome_executable():
    """查找本机完整 Chrome；Windows 同时检查常见安装目录和注册表。"""
    sysname = platform.system().lower()
    if "darwin" in sysname:
        path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        return path if os.path.exists(path) else None
    if "windows" in sysname:
        candidates = []
        for variable in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(variable)
            if base:
                candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
        for key in (r"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
                    r"HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"):
            try:
                out = subprocess.check_output(f'reg query "{key}" /ve', shell=True,
                                              stderr=subprocess.DEVNULL).decode(errors="ignore")
                match = re.search(r"REG_SZ\s+(.+chrome\.exe)", out, re.IGNORECASE)
                if match:
                    candidates.append(match.group(1).strip())
            except Exception:
                pass
        for path in candidates:
            if os.path.isfile(path):
                return path
        return None
    for executable in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        try:
            subprocess.check_output([executable, "--version"], stderr=subprocess.DEVNULL)
            return executable
        except Exception:
            continue
    return None


def detect_chrome_version(executable=None):
    """检测本机 Chrome 主版本号；未指定路径时自动查找。"""
    return _chrome_major(executable or find_chrome_executable())


def platform_tag():
    """返回 (chrome-for-testing 平台目录名, driver 可执行文件名)。"""
    sysname = platform.system().lower()
    machine = platform.machine().lower()
    if "darwin" in sysname:
        return ("mac-arm64" if machine in ("arm64", "aarch64") else "mac-x64", "chromedriver")
    if "windows" in sysname:
        return ("win64" if machine.endswith("64") else "win32", "chromedriver.exe")
    return ("linux64", "chromedriver")


def _mirror_versions():
    """从淘宝镜像列出全部可用版本（用于挑选最接近的）。"""
    url = f"{CFT_MIRROR}/"
    try:
        data = urllib.request.urlopen(url, timeout=15).read().decode()
        return re.findall(r'"name":"(\d+\.\d+\.\d+\.\d+)/"', data)
    except Exception:
        return []


def _pick_version(major):
    """挑选与本机 Chrome 主版本号匹配、且 build 号最大的镜像版本。"""
    vers = [v for v in _mirror_versions() if v.split(".")[0] == str(major)]
    if not vers:
        return None
    vers.sort(key=lambda v: [int(x) for x in v.split(".")])
    return vers[-1]


def _latest_mirror_version():
    """取镜像中版本号最高的 Chrome for Testing 版本。"""
    versions = _mirror_versions()
    if not versions:
        return None
    return max(versions, key=lambda v: [int(x) for x in v.split(".")])


def _download_with_progress(url, target, label):
    """下载到临时文件后原子替换，并在终端显示实际字节进度。"""
    temporary = target + ".part"
    downloaded = 0
    last_draw = 0.0
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=30) as response, open(temporary, "wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                now = time.time()
                if now - last_draw >= 0.12 or (total and downloaded >= total):
                    if total:
                        percent = min(100, downloaded * 100 // total)
                        filled = percent * 24 // 100
                        bar = "#" * filled + "-" * (24 - filled)
                        text = f"  {label}: [{bar}] {percent:3d}% ({downloaded / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MB)"
                    else:
                        text = f"  {label}: {downloaded / 1024 / 1024:.1f} MB"
                    print("\r" + text, end="", flush=True)
                    last_draw = now
        if downloaded < 1:
            raise RuntimeError("下载文件为空")
        os.replace(temporary, target)
        print()
        return downloaded
    except Exception:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def _download_cft(relative_path, target, label):
    """优先 npmmirror，失败后回退 Chrome 官方源，并记录每个真实地址。"""
    errors = []
    for name, base in (("npmmirror", CFT_MIRROR), ("Chrome 官方源", CFT_OFFICIAL)):
        url = f"{base}/{relative_path}"
        log(f"{label}：从{name}下载", "INFO")
        log(f"下载地址：{url}", "INFO")
        try:
            size = _download_with_progress(url, target, label)
            log(f"{label} 下载完成（{size / 1024 / 1024:.1f} MB）", "SUCCESS")
            return True
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            log(f"{name} 下载失败：{exc}", "WARN")
    log(f"{label} 无法下载；" + "；".join(errors), "ERROR")
    return False


def download_driver(major, full=None):
    """下载匹配本机平台与 Chrome 版本的 chromedriver，返回可执行路径。"""
    tag, exe_name = platform_tag()
    os.makedirs(DRIVER_DIR, exist_ok=True)
    full = full or _pick_version(major)
    if not full:
        log(f"镜像上未找到 Chrome {major} 对应的 driver", "WARN")
        return None
    # 用完整版本名缓存，避免同一主版本升级后误复用不匹配的 driver。
    dest = os.path.join(DRIVER_DIR, f"{tag}-{full}-{exe_name}")
    if os.path.exists(dest) and os.path.getsize(dest) > 1_000_000:
        return dest
    log(f"下载 chromedriver {full} ({tag}) ...", "INFO")
    zip_path = os.path.join(DRIVER_DIR, "cd.zip")
    if not _download_cft(f"{full}/{tag}/chromedriver-{tag}.zip", zip_path, "chromedriver"):
        return None

    # 解压，取出 chromedriver 可执行文件（排除 LICENSE.chromedriver 等同后缀文件）
    extracted = False
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            base = name.rsplit("/", 1)[-1]
            if base == exe_name:  # 精确匹配文件名，而非 endswith
                with z.open(name) as src, open(dest, "wb") as out:
                    out.write(src.read())
                extracted = True
                break
    try:
        os.remove(zip_path)
    except Exception:
        pass
    if not extracted or not os.path.exists(dest):
        log("解压未找到 chromedriver", "ERROR")
        return None
    if not IS_WIN:
        os.chmod(dest, 0o755)
    log("chromedriver 下载完成", "SUCCESS")
    return dest


def prepare_driver(major, full=None):
    """下载（或复用缓存）+ Mac 去隔离签名，返回可用的 driver 路径；失败返回 None。"""
    path = download_driver(major, full)
    if not path:
        return None
    if IS_MAC:
        os.system(f"xattr -c '{path}' 2>/dev/null")
        subprocess.run(f"codesign --force --deep --sign - '{path}'",
                       shell=True, capture_output=True)
    return path


def download_portable_chrome():
    """Windows 未安装 Chrome 时下载完整 Chrome for Testing，返回 (路径, 完整版本)。"""
    if not IS_WIN:
        return None, None
    tag, _ = platform_tag()
    os.makedirs(BROWSER_DIR, exist_ok=True)

    # 先复用之前已经完整解压的浏览器，避免重复下载 100MB 以上的文件。
    for name in sorted(os.listdir(BROWSER_DIR), reverse=True):
        candidate = os.path.join(BROWSER_DIR, name, f"chrome-{tag}", "chrome.exe")
        if os.path.isfile(candidate) and os.path.getsize(candidate) > 100_000:
            version = name.rsplit("-", 1)[-1]
            log(f"复用已下载的 Chrome for Testing（版本 {version}）", "SUCCESS")
            return candidate, version

    full = _latest_mirror_version()
    if not full:
        full = CFT_FALLBACK_VERSION
        log(f"无法读取版本列表，改用已验证的 Chrome for Testing {full}", "WARN")
    target_dir = os.path.join(BROWSER_DIR, f"{tag}-{full}")
    executable = os.path.join(target_dir, f"chrome-{tag}", "chrome.exe")
    zip_path = os.path.join(BROWSER_DIR, "chrome-for-testing.zip")
    log("未检测到本机 Chrome，准备下载完整 Chrome for Testing（非无头浏览器）", "WARN")
    if not _download_cft(f"{full}/{tag}/chrome-{tag}.zip", zip_path, "Chrome 浏览器"):
        return None, None
    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(target_dir)
    except Exception as exc:
        log(f"Chrome 浏览器解压失败：{exc}", "ERROR")
        return None, None
    finally:
        try:
            os.remove(zip_path)
        except OSError:
            pass
    if not os.path.isfile(executable):
        log(f"解压后未找到 Chrome：{executable}", "ERROR")
        return None, None
    log("Chrome 浏览器准备完成", "SUCCESS")
    return executable, full


def start_driver_with_watchdog(launcher, backend, timeout=45):
    """启动 WebDriver 时显示心跳；连接卡住时给出明确错误而非无限等待。"""
    state = {"driver": None, "error": None}
    done = threading.Event()

    def launch():
        try:
            state["driver"] = launcher()
        except BaseException as exc:
            state["error"] = exc
        finally:
            done.set()

    log(f"正在通过 {backend} 启动 Chrome 并连接 WebDriver ...", "INFO")
    worker = threading.Thread(target=launch, name="chrome-startup", daemon=True)
    worker.start()
    started = time.time()
    while not done.wait(0.2):
        elapsed = int(time.time() - started)
        print(f"\r  Chrome 已打开，正在等待 WebDriver 连接 ... {elapsed:02d}s", end="", flush=True)
        if elapsed >= timeout:
            print()
            force_kill_chrome()
            raise TimeoutError(
                f"Chrome 已启动，但 WebDriver 在 {timeout} 秒内没有完成连接。"
                "已关闭本次浏览器，请重新运行并查看上方浏览器/驱动下载日志。"
            )
    print()
    if state["error"]:
        raise state["error"]
    log("Chrome 已由 WebDriver 接管", "SUCCESS")
    return state["driver"]


def start_chrome_with_watchdog(options, kwargs, timeout=45):
    """macOS/Linux 保留 undetected-chromedriver 启动方式。"""
    return start_driver_with_watchdog(
        lambda: uc.Chrome(options=options, **kwargs), "undetected-chromedriver", timeout
    )


def start_windows_chrome(browser_path, driver_path):
    """Windows 直接使用 Selenium 官方协议，绕过 UC 可能卡住的补丁启动过程。"""
    if not browser_path or not driver_path:
        raise RuntimeError("Windows 启动浏览器缺少项目 Chrome 或 chromedriver 路径。")
    options = webdriver.ChromeOptions()
    options.binary_location = browser_path
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-blink-features=AutomationControlled")
    service = Service(executable_path=driver_path)
    return start_driver_with_watchdog(
        lambda: webdriver.Chrome(service=service, options=options), "Selenium 官方驱动", 30
    )


# ================= 统一身份认证信息管理 =================
ENV_USER_KEY = "SWU_UNIFIED_ID_USERNAME"
ENV_PASSWORD_KEY = "SWU_UNIFIED_ID_PASSWORD"
# 兼容升级前已经写入 .env 的键名，读取后自动迁移为上面的新键名。
LEGACY_ENV_USER_KEY = "ZHENGFANG_USERNAME"
LEGACY_ENV_PASSWORD_KEY = "ZHENGFANG_PASSWORD"


def read_env_file(path):
    """读取简单的 .env 文件；支持由 json.dumps 写出的带引号值。"""
    values = {}
    if not os.path.exists(path):
        return values
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw = line.split("=", 1)
                key, raw = key.strip(), raw.strip()
                if not key:
                    continue
                if raw.startswith('"'):
                    try:
                        values[key] = json.loads(raw)
                        continue
                    except Exception:
                        pass
                values[key] = raw.strip("'\"")
    except Exception:
        return {}
    return values


def write_env_values(updates):
    """仅更新统一身份认证键，保留 .env 内用户自行添加的其他配置。"""
    lines = []
    seen = set()
    if os.path.exists(ENV_FILE):
        try:
            with open(ENV_FILE, "r", encoding="utf-8") as f:
                for line in f.read().splitlines():
                    key = line.split("=", 1)[0].strip() if "=" in line else ""
                    if key in updates:
                        lines.append(f"{key}={json.dumps(updates[key], ensure_ascii=False)}")
                        seen.add(key)
                    else:
                        lines.append(line)
        except Exception:
            lines = []
    for key, value in updates.items():
        if key not in seen:
            lines.append(f"{key}={json.dumps(value, ensure_ascii=False)}")
    with open(ENV_FILE, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines).rstrip()+"\n")
    # 尽量收紧权限（Windows 会忽略该设置，仍可正常使用）。
    try:
        os.chmod(ENV_FILE, 0o600)
    except Exception:
        pass


def load_account():
    """优先读取 .env；兼容旧键名和旧 account.json，并自动迁移。"""
    env = read_env_file(ENV_FILE)
    username = os.environ.get(ENV_USER_KEY) or env.get(ENV_USER_KEY)
    password = os.environ.get(ENV_PASSWORD_KEY) or env.get(ENV_PASSWORD_KEY)
    if username and password:
        return username, password

    # 一次性兼容升级前的 .env，避免改名后要求用户再次输入统一身份认证信息。
    legacy_username = os.environ.get(LEGACY_ENV_USER_KEY) or env.get(LEGACY_ENV_USER_KEY)
    legacy_password = os.environ.get(LEGACY_ENV_PASSWORD_KEY) or env.get(LEGACY_ENV_PASSWORD_KEY)
    if legacy_username and legacy_password:
        if env.get(LEGACY_ENV_USER_KEY) and env.get(LEGACY_ENV_PASSWORD_KEY):
            write_env_values({ENV_USER_KEY: legacy_username, ENV_PASSWORD_KEY: legacy_password})
            log("已将旧 .env 中的登录信息迁移为统一身份认证配置", "SUCCESS")
        return legacy_username, legacy_password

    # 一次性兼容更早版本的数据，避免升级后要求用户再次输入统一身份认证信息。
    if os.path.exists(LEGACY_ACCOUNT_FILE):
        try:
            with open(LEGACY_ACCOUNT_FILE, "r", encoding="utf-8") as f:
                legacy = json.load(f)
            username, password = legacy.get("username"), legacy.get("password")
            if username and password:
                write_env_values({ENV_USER_KEY: username, ENV_PASSWORD_KEY: password})
                log("已将旧 account.json 中的登录信息迁移到 .env", "SUCCESS")
                return username, password
        except Exception:
            pass
    return None, None


def save_account(username, password):
    try:
        write_env_values({ENV_USER_KEY: username, ENV_PASSWORD_KEY: password})
        log("统一身份认证信息已保存到本地 .env（下次免输入）", "SUCCESS")
    except Exception as e:
        log(f"登录信息保存失败: {e}", "WARN")


def prompt_account_windows_gui():
    """使用 Windows 自带 WPF 显示登录窗口；不可用时返回 None 供其他方案兜底。"""
    script = r'''
$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName PresentationFramework
    [xml]$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="西南大学课程表导出" Width="520" Height="530" ResizeMode="NoResize"
        WindowStartupLocation="CenterScreen" Background="#F3F6FB" FontFamily="Microsoft YaHei UI">
  <Grid Margin="24">
    <Border Background="White" CornerRadius="18" Padding="30">
      <StackPanel KeyboardNavigation.TabNavigation="Cycle">
        <Border Background="#EAF2FF" CornerRadius="12" Padding="12" Margin="0,0,0,16">
          <TextBlock Text="SWU 课程表" Foreground="#1458B8" FontSize="16" FontWeight="SemiBold"/>
        </Border>
        <TextBlock Text="西南大学统一身份认证" FontSize="23" FontWeight="SemiBold" Foreground="#172033"/>
        <TextBlock Text="登录信息仅保存在本机 .env 文件中，用于自动登录教务系统。" Margin="0,7,0,22" TextWrapping="Wrap" Foreground="#667085" FontSize="13"/>
        <TextBlock Text="统一认证账号" Foreground="#344054" FontWeight="SemiBold" Margin="0,0,0,6" TextAlignment="Center"/>
        <TextBox x:Name="UsernameBox" Height="42" Padding="11,0" FontSize="14" BorderBrush="#CBD5E1" TextAlignment="Center" HorizontalContentAlignment="Center" VerticalContentAlignment="Center"/>
        <TextBlock Text="密码" Foreground="#344054" FontWeight="SemiBold" Margin="0,16,0,6" TextAlignment="Center"/>
        <PasswordBox x:Name="PasswordBox" Height="42" Padding="11,0" FontSize="14" BorderBrush="#CBD5E1" HorizontalContentAlignment="Center" VerticalContentAlignment="Center"/>
        <CheckBox x:Name="RememberBox" IsChecked="True" Content="记住登录信息（下次免输入）" Foreground="#475467" Margin="0,15,0,20"/>
        <StackPanel Orientation="Horizontal" HorizontalAlignment="Right">
          <Button x:Name="CancelButton" Content="取消" Width="82" Height="38" Margin="0,0,10,0" Background="White" BorderBrush="#CBD5E1" Foreground="#344054" IsCancel="True"/>
          <Button x:Name="LoginButton" Content="保存并继续" Width="112" Height="38" Background="#1769E0" BorderThickness="0" Foreground="White" FontWeight="SemiBold" IsDefault="True"/>
        </StackPanel>
      </StackPanel>
    </Border>
  </Grid>
</Window>
'@
    $reader = New-Object System.Xml.XmlNodeReader $xaml
    $window = [Windows.Markup.XamlReader]::Load($reader)
    $usernameBox = $window.FindName('UsernameBox')
    $passwordBox = $window.FindName('PasswordBox')
    $rememberBox = $window.FindName('RememberBox')
    $loginButton = $window.FindName('LoginButton')
    $cancelButton = $window.FindName('CancelButton')
    $script:result = $null
    $submit = {
        $username = $usernameBox.Text.Trim()
        $password = $passwordBox.Password
        if ([string]::IsNullOrWhiteSpace($username) -or [string]::IsNullOrWhiteSpace($password)) {
            [System.Windows.MessageBox]::Show('请填写统一身份认证账号和密码。', '提示', 'OK', 'Warning') | Out-Null
            return
        }
        $script:result = [pscustomobject]@{ username = $username; password = $password; remember = [bool]$rememberBox.IsChecked }
        $window.DialogResult = $true
        $window.Close()
    }
    $loginButton.Add_Click($submit)
    $cancelButton.Add_Click({ $window.Close() })
    $window.Add_KeyDown({ param($sender, $eventArgs) if ($eventArgs.Key -eq 'Enter') { & $submit } })
    $usernameBox.Focus() | Out-Null
    $window.ShowDialog() | Out-Null
    if ($null -ne $script:result) {
        $json = $script:result | ConvertTo-Json -Compress
        [Console]::Out.WriteLine([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)))
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
'''
    try:
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", encoded],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except Exception as exc:
        log(f"无法启动 Windows 登录窗口：{exc}", "WARN")
        return None
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        log(f"Windows 登录窗口不可用：{detail[-1] if detail else '未知错误'}", "WARN")
        return None
    payload = next((line.strip() for line in reversed(completed.stdout.splitlines()) if line.strip()), "")
    if not payload:
        return None, None, False  # 用户正常取消
    try:
        values = json.loads(base64.b64decode(payload).decode("utf-8"))
        return values.get("username"), values.get("password"), bool(values.get("remember"))
    except Exception:
        log("Windows 登录窗口返回的数据无效，已改用备用输入方式", "WARN")
        return None


def prompt_account_gui():
    """弹出图形窗口让用户输入统一身份认证信息。返回 (username, password, remember)。
    无图形环境时回退到终端输入。"""
    if IS_WIN:
        result = prompt_account_windows_gui()
        if result is not None:
            return result
    try:
        import tkinter as tk
        from tkinter import messagebox, ttk
    except Exception:
        return prompt_account_cli()

    result = {"u": None, "p": None, "remember": True, "ok": False}

    root = tk.Tk()
    root.title("西南大学统一身份认证")
    root.resizable(False, False)
    root.configure(bg="#f3f6fb")
    W, H = 430, 282
    root.update_idletasks()
    x = (root.winfo_screenwidth() - W) // 2
    y = (root.winfo_screenheight() - H) // 2
    root.geometry(f"{W}x{H}+{x}+{y}")

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("LoginCard.TFrame", background="#ffffff")
    style.configure("LoginTitle.TLabel", background="#ffffff", foreground="#172033",
                    font=("Microsoft YaHei", 15, "bold"))
    style.configure("LoginNote.TLabel", background="#ffffff", foreground="#667085",
                    font=("Microsoft YaHei", 9))
    style.configure("LoginField.TLabel", background="#ffffff", foreground="#344054",
                    font=("Microsoft YaHei", 10))
    style.configure("Login.TCheckbutton", background="#ffffff", font=("Microsoft YaHei", 9))
    style.configure("Login.TButton", background="#1769e0", foreground="#ffffff",
                    font=("Microsoft YaHei", 10, "bold"), padding=(12, 7))
    style.map("Login.TButton", background=[("active", "#1259c2")])

    card = ttk.Frame(root, style="LoginCard.TFrame", padding=(28, 22, 28, 20))
    card.pack(fill="both", expand=True, padx=12, pady=12)
    ttk.Label(card, text="西南大学统一身份认证", style="LoginTitle.TLabel").pack(anchor="w")
    ttk.Label(card, text="登录信息仅保存在本机 .env 中，用于自动登录",
              style="LoginNote.TLabel").pack(anchor="w", pady=(4, 15))

    frm = ttk.Frame(card, style="LoginCard.TFrame"); frm.pack(fill="x")
    frm.columnconfigure(1, weight=1)
    ttk.Label(frm, text="账号", style="LoginField.TLabel", width=7).grid(row=0, column=0, pady=(0, 10), sticky="w")
    e_user = ttk.Entry(frm, width=30); e_user.grid(row=0, column=1, pady=(0, 10), sticky="ew")
    ttk.Label(frm, text="密码", style="LoginField.TLabel", width=7).grid(row=1, column=0, pady=(0, 4), sticky="w")
    e_pwd = ttk.Entry(frm, width=30, show="•"); e_pwd.grid(row=1, column=1, pady=(0, 4), sticky="ew")

    var_remember = tk.BooleanVar(value=True)
    ttk.Checkbutton(card, text="记住登录信息（下次免输入）", variable=var_remember,
                    style="Login.TCheckbutton").pack(anchor="w", pady=(8, 10))

    def close_dialog():
        """先立即隐藏，再由 Tk 的空闲回调销毁，避免 Windows 遗留前台窗口。"""
        try:
            root.withdraw()
            root.update_idletasks()
        except Exception:
            pass
        root.after_idle(root.destroy)

    def submit():
        u, p = e_user.get().strip(), e_pwd.get().strip()
        if not u or not p:
            messagebox.showwarning("提示", "请填写统一身份认证账号和密码")
            return
        result.update(u=u, p=p, remember=var_remember.get(), ok=True)
        close_dialog()

    def cancel():
        close_dialog()

    ttk.Button(card, text="登录", width=12, command=submit, style="Login.TButton").pack(anchor="e")
    e_user.focus_set()
    root.bind("<Return>", lambda e: submit())
    root.protocol("WM_DELETE_WINDOW", cancel)
    root.mainloop()
    try:
        root.destroy()
    except Exception:
        pass

    if not result["ok"]:
        return None, None, False
    return result["u"], result["p"], result["remember"]


def prompt_account_cli():
    """终端输入回退方案。"""
    import getpass
    print("\n请输入西南大学统一身份认证账号（仅保存在本机 .env）：")
    u = input("  统一认证账号: ").strip()
    p = getpass.getpass("  密码: ").strip()
    ans = input("  记住登录信息？下次免输入 [Y/n]: ").strip().lower()
    return (u or None), (p or None), (ans != "n")


def resolve_account():
    """优先用本地保存的统一身份认证信息；没有则输入并按需保存。"""
    global USERNAME, PASSWORD
    u, p = load_account()
    if u and p:
        log("已加载本地统一身份认证信息", "SUCCESS")
        USERNAME, PASSWORD = u, p
        return True
    u, p, remember = prompt_account_gui()
    if not u or not p:
        log("未输入统一身份认证信息，已取消", "WARN")
        return False
    USERNAME, PASSWORD = u, p
    if remember:
        save_account(u, p)
    return True


# ================= 登录 =================
# 全局 OCR 实例（兼容不同 ddddocr 版本：新版无 show_ad 参数）
def _make_ocr():
    try:
        return ddddocr.DdddOcr(show_ad=False)
    except TypeError:
        try:
            return ddddocr.DdddOcr()
        except Exception as e:
            log(f"ddddocr 初始化失败: {e}", "ERROR")
            return None
_OCR = _make_ocr()


def get_captcha(driver):
    try:
        img = WebDriverWait(driver, 5).until(
            EC.visibility_of_element_located((By.ID, ID_IMG)))
        size = img.size
        if size['width'] == 0:
            log(f"验证码图片宽度为0，可能未加载（id={ID_IMG}）", "WARN")
            return ""
        png = img.screenshot_as_png
        if _OCR is None:
            return ""
        raw = _OCR.classification(png)
        # 只保留字母数字，正方验证码通常是 4 位
        code = "".join(ch for ch in raw if ch.isalnum())
        log(f"OCR 识别: 原始'{raw}' → 清洗'{code}' (图片 {size['width']}x{size['height']})", "INFO")
        return code
    except Exception as e:
        log(f"验证码获取异常: {e}", "WARN")
        return ""


def login_procedure(driver):
    wait = WebDriverWait(driver, 15)
    try:
        log("正在打开统一身份认证页面 ...", "INFO")
        driver.get(URL_START)
        log("统一身份认证页面已载入，正在填写登录信息 ...", "INFO")
        time.sleep(3)

        # 有些入口需要先点「登录」div
        try:
            btn = wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//div[contains(@onclick, '_goLogin')]")))
            driver.execute_script("arguments[0].click();", btn)
        except Exception:
            pass
        time.sleep(3)

        code = get_captcha(driver)
        if len(code) != 4:
            log("验证码位数不对，刷新重试", "WARN")
            driver.refresh()
            return False

        log("填写统一身份认证信息...", "INFO")
        driver.execute_script(f"document.getElementById('{ID_USER}').value=arguments[0];", USERNAME)
        driver.execute_script(f"document.getElementById('{ID_PWD}').value=arguments[0];", PASSWORD)
        driver.execute_script(f"document.getElementById('{ID_CODE}').value=arguments[0];", code)

        driver.execute_script("verifyCode();")
        time.sleep(1)

        try:
            tishi_src = driver.find_element(By.ID, ID_TISHI).get_attribute("src") or ""
            if "code_error" in tishi_src:
                log("验证码被拒绝", "WARN")
                driver.refresh()
                return False
        except Exception:
            pass

        log("提交登录请求...", "INFO")
        driver.execute_script("portalLogin();")
        time.sleep(5)

        if "login" not in driver.current_url:
            log("登录成功！", "SUCCESS")
            return True
        log("登录未跳转，可能密码/验证码错误", "WARN")
        return False
    except Exception as e:
        log(f"登录流程出错: {e}", "ERROR")
        return False


# ================= 学年 / 学期选择与课表抓取 =================
# 仅用于旧版或非标准页面的后备抓取。新版正方优先读取表格视图（#table1）。
COURSE_SELECTORS = ["div.timetable_con", "td[id^='jc_']", "#kbtable", "table.kbcontent"]


def _html_text(fragment):
    """去除 HTML 标签并还原实体，保留页面中的实际可见文字。"""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def _html_attr(attrs, name):
    match = re.search(r"\b" + re.escape(name) + r"\s*=\s*(['\"])(.*?)\1", attrs,
                      flags=re.I | re.S)
    return unescape(match.group(2)).strip() if match else ""


def _parse_select_from_html(source, select_id):
    """直接从 page_source 解析正方原生 select，不依赖动态控件状态。"""
    for attrs, body in re.findall(r"<select\b([^>]*)>(.*?)</select\s*>", source,
                                  flags=re.I | re.S):
        if _html_attr(attrs, "id") != select_id and _html_attr(attrs, "name") != select_id:
            continue
        options, by_index, selected = [], {}, None
        raw_options = re.findall(r"<option\b([^>]*)>(.*?)</option\s*>", body, flags=re.I | re.S)
        for index, (option_attrs, option_body) in enumerate(raw_options):
            value = _html_attr(option_attrs, "value")
            text = _html_text(option_body)
            by_index[index] = value
            if not value or not text or text == "---请选择---":
                continue
            item = {"value": value, "text": text, "index": index}
            options.append(item)
            if re.search(r"\bselected\b", option_attrs, flags=re.I):
                selected = item
        if selected is None and options:
            selected = options[0]
        return {"options": options, "by_index": by_index, "selected": selected}
    return {"options": [], "by_index": {}, "selected": None}


def _parse_chosen_from_html(source, select_id, native):
    """直接解析 Chosen 容器内的 li；截图中的完整学年列表来自这里。"""
    container = re.search(r"\bid\s*=\s*(['\"])" + re.escape(select_id) + r"_chosen\1",
                          source, flags=re.I)
    if not container:
        return {"options": [], "selected": None}
    tail = source[container.end():]
    results = re.search(r"<ul\b[^>]*\bclass\s*=\s*(['\"])[^'\"]*\bchosen-results\b[^'\"]*\1[^>]*>",
                        tail, flags=re.I | re.S)
    if not results:
        return {"options": [], "selected": None}
    closing = re.search(r"</ul\s*>", tail[results.end():], flags=re.I)
    if not closing:
        return {"options": [], "selected": None}
    body = tail[results.end():results.end() + closing.start()]
    options, selected = [], None
    for attrs, li_body in re.findall(r"<li\b([^>]*)>(.*?)</li\s*>", body, flags=re.I | re.S):
        text = _html_text(li_body)
        index_raw = _html_attr(attrs, "data-option-array-index")
        if not text or text == "---请选择---" or not index_raw.isdigit():
            continue
        index = int(index_raw)
        item = {"value": native["by_index"].get(index, ""), "text": text, "index": index}
        options.append(item)
        if "result-selected" in _html_attr(attrs, "class").split():
            selected = item
    return {"options": options, "selected": selected}


def get_term_info(driver):
    """直接从当前页面 HTML 解析 select 和 Chosen 列表，不需要先点开下拉框。"""
    source = driver.page_source

    def build(select_id):
        native = _parse_select_from_html(source, select_id)
        chosen = _parse_chosen_from_html(source, select_id, native)
        options = chosen["options"] if len(chosen["options"]) > len(native["options"]) else native["options"]
        selected = native["selected"] or chosen["selected"]
        if selected is None and options:
            selected = options[0]
        return {
            "value": selected["value"] if selected else "",
            "text": selected["text"] if selected else "",
            "options": options
        }

    title = re.search(r"<[^>]*\bclass\s*=\s*(['\"])[^'\"]*\btimetable_title\b[^'\"]*\1[^>]*>(.*?)</[^>]+>",
                      source, flags=re.I | re.S)
    return {
        "academicYear": build("xnm"),
        "term": build("xqm"),
        "timetableTitle": _html_text(title.group(2)) if title else ""
    }


def wait_for_term_options(driver, timeout=12):
    """等待页面 HTML 填充学年/学期选项；不操作浏览器中的下拉控件。"""
    deadline = time.time() + timeout
    latest = {}
    while time.time() < deadline:
        latest = get_term_info(driver)
        year = latest.get("academicYear") or {}
        term = latest.get("term") or {}
        if len(year.get("options", [])) > 1 and len(term.get("options", [])) > 1:
            return latest
        time.sleep(0.4)
    return latest or get_term_info(driver)


def option_texts(options):
    """把页面原有的选项完整显示给用户，不截断历史学年。"""
    return "、".join(o.get("text", "") for o in options if o.get("text")) or "（页面暂未提供选项）"


def set_select_by_label(driver, element_id, wanted):
    """按显示文字（或底层 value）设置下拉框，必要时直接点击 Chosen 选项。"""
    return driver.execute_script("""
    var id = arguments[0], wanted = String(arguments[1]).trim();
    var el = document.getElementById(id);
    if (!el) return {ok: false, reason: '未找到下拉框'};
    var option = Array.prototype.find.call(el.options, function (o) {
      return (o.textContent || '').trim() === wanted || o.value === wanted;
    });
    if (option) {
      el.value = option.value;
      if (window.jQuery) {
        window.jQuery(el).trigger('change').trigger('chosen:updated');
      } else {
        el.dispatchEvent(new Event('change', {bubbles: true}));
      }
      return {ok: true, value: option.value, text: (option.textContent || '').trim()};
    }
    // 有些正方页面只在 Chosen 视图中保留完整选项；先展开，再交给控件同步。
    var chosen = document.getElementById(id + '_chosen');
    if (window.jQuery) window.jQuery(el).trigger('chosen:open');
    else {
      var trigger = chosen && chosen.querySelector('.chosen-single');
      if (trigger) trigger.click();
    }
    var item = chosen && Array.prototype.find.call(
      chosen.querySelectorAll('.chosen-results li.active-result'),
      function (li) { return (li.textContent || '').trim() === wanted; }
    );
    if (!item) return {ok: false, reason: '不存在该选项'};
    item.click();
    var selected = el.options && el.options[el.selectedIndex];
    return {
      ok: true,
      value: selected ? selected.value : '',
      text: selected ? (selected.textContent || '').trim() : wanted
    };
    """, element_id, wanted)


def wait_for_selected_timetable(driver, year_text, term_text, previous_title, timeout=12):
    """等待 Ajax 查询完成，并确认标题已经切换到用户实际选择的学期。"""
    deadline = time.time() + timeout
    latest = {}
    wanted_term = f"{term_text}学期" if term_text else ""
    while time.time() < deadline:
        latest = get_term_info(driver)
        title = latest.get("timetableTitle", "")
        if (title != previous_title and year_text and year_text in title and
                (not wanted_term or wanted_term in title)):
            return latest
        time.sleep(0.5)
    title = latest.get("timetableTitle", "")
    log(f"查询后未确认课表标题切换（当前：{title or '未读取到'}）", "WARN")
    return latest or get_term_info(driver)


def choose_academic_term(driver):
    """让用户按需选择历史/未来学期；直接回车则保留页面当前学期。"""
    info = wait_for_term_options(driver)
    year = info.get("academicYear") or {}
    term = info.get("term") or {}
    if not year or not term:
        log("页面没有标准的学年/学期下拉框，将抓取当前课表", "WARN")
        return info

    original_year = year.get("value")
    original_term = term.get("value")
    original_title = info.get("timetableTitle", "")
    selected_year = {"value": original_year, "text": year.get("text", "")}
    selected_term = {"value": original_term, "text": term.get("text", "")}
    changed = False
    print("\n  选择要导出的课表（直接回车保留当前选择）：")
    print(f"  当前：{year.get('text') or '未选择'} 学年，第 {term.get('text') or '未选择'} 学期")
    print(f"  可选学年：{option_texts(year.get('options', []))}")
    try:
        wanted_year = input(f"  学年 [{year.get('text', '')}]: ").strip()
        if wanted_year:
            result = set_select_by_label(driver, "xnm", wanted_year)
            if not result.get("ok"):
                log(f"学年“{wanted_year}”不可用，保留当前选择", "WARN")
            else:
                log(f"已选择学年：{result['text']}", "SUCCESS")
                selected_year = result
                changed = (result.get("value") != original_year or
                           result.get("text") != year.get("text"))
                # 学期选项与学年关联，切换后等待页面重新填充，再读取一次。
                info = wait_for_term_options(driver)
                term = info.get("term") or {}
        else:
            info = get_term_info(driver)
            term = info.get("term") or term

        print(f"  可选学期：{option_texts(term.get('options', []))}")
        wanted_term = input(f"  学期 [{term.get('text', '')}]: ").strip()
        if wanted_term:
            result = set_select_by_label(driver, "xqm", wanted_term)
            if not result.get("ok"):
                log(f"学期“{wanted_term}”不可用，保留当前选择", "WARN")
            else:
                log(f"已选择学期：第 {result['text']} 学期", "SUCCESS")
                selected_term = result
                changed = (changed or result.get("value") != original_term or
                           result.get("text") != term.get("text"))
    except (EOFError, KeyboardInterrupt):
        print()
        log("未输入学年/学期，保留页面当前选择", "INFO")
        return get_term_info(driver)

    if changed:
        log("正在查询所选学年/学期的课表...", "INFO")
        clicked = driver.execute_script("""
        var btn = document.getElementById('search_go');
        if (!btn) return false;
        btn.click();
        return true;
        """)
        if not clicked:
            log("未找到课表查询按钮，无法切换学期", "ERROR")
            return get_term_info(driver)
        return wait_for_selected_timetable(
            driver, selected_year.get("text", ""), selected_term.get("text", ""), original_title
        )
    return get_term_info(driver)


def grab_schedule_html(driver):
    """优先抓取与正方「表格」视图一致的完整 DOM，保留所有课程字段。"""
    js = """
    var SELS = arguments[0];
    function selected(id) {
      var el = document.getElementById(id);
      var opt = el && el.options && el.options[el.selectedIndex];
      return opt ? {value: opt.value, text: (opt.textContent || '').trim()} : null;
    }
    var form = document.querySelector('#ajaxForm');
    var table = document.querySelector(
      '#ylkbTable #table1 table.timetable1, #ylkbTable #table1 table.timetable, ' +
      '#ylkbTable #kbgrid_table_0, #table1 table.timetable1, #table1 table');
    var title = table && table.querySelector('.timetable_title');
    var meta = {
      academicYear: selected('xnm'),
      term: selected('xqm'),
      timetableTitle: title ? (title.textContent || '').replace(/\\s+/g, ' ').trim() : ''
    };
    if (table && table.querySelector('.timetable_con')) {
      return {
        html: '<section id="schedule-capture">' +
          (form ? form.outerHTML : '') + table.outerHTML + '</section>',
        hit: table.querySelectorAll('.timetable_con').length,
        mode: 'timetable-grid',
        meta: meta
      };
    }

    var parts = [], seen = [];
    for (var s = 0; s < SELS.length; s++) {
      var els = document.querySelectorAll(SELS[s]);
      for (var i = 0; i < els.length; i++) {
        var tr = els[i].closest('tr') || els[i];
        if (seen.indexOf(tr) >= 0) continue;
        seen.push(tr); parts.push(tr.outerHTML);
      }
    }
    if (parts.length < 2) {
      return {html: document.body.outerHTML, hit: parts.length, mode: 'fullpage', meta: meta};
    }
    return {html: '<table>' + parts.join('\\n') + '</table>', hit: parts.length,
            mode: 'selector', meta: meta};
    """
    return driver.execute_script(js, COURSE_SELECTORS)


def write_captured(html, meta):
    payload = {"html": html, "meta": meta,
               "capturedAt": time.strftime("%Y-%m-%d %H:%M:%S")}
    with open(CAPTURED_JS, "w", encoding="utf-8") as f:
        f.write("window.CAPTURED_SCHEDULE = " +
                json.dumps(payload, ensure_ascii=False) + ";\n")
    log(f"已写入抓取结果 → captured.js", "SUCCESS")


# ================= 主程序 =================
def main():
    print("=" * 56)
    print("   📅  西南大学课程表自动抓取器")
    print("=" * 56)

    # 1) 账号（本地已存则免输入，否则显示登录窗口）
    if not resolve_account():
        return

    # 2) 代理探测
    proxy_on = setup_proxy()
    log(f"网络模式: {'走代理 '+PROXY if proxy_on else '直连（未检测到代理）'}", "SYSTEM")

    force_kill_chrome()

    driver = None
    try:
        # 3) Windows 固定使用项目目录的完整 Chrome for Testing；其他系统优先系统 Chrome。
        #    路径显式交给 uc，避免其自行寻找/下载无头浏览器失败。
        portable_version = None
        if IS_WIN:
            # Windows 始终使用项目下载的 Chrome for Testing，绝不接管用户日常浏览器。
            browser_path, portable_version = download_portable_chrome()
            if not browser_path:
                raise RuntimeError("项目专用 Chrome for Testing 下载失败。请检查网络后重试。")
        else:
            browser_path = find_chrome_executable()
        major = detect_chrome_version(browser_path)
        if not major and portable_version:
            major = int(portable_version.split(".")[0])
        if major:
            log(f"使用 Chrome 主版本: {major}", "INFO")
        else:
            log("未能检测 Chrome 版本，将由 undetected-chromedriver 处理浏览器", "WARN")
        driver_path = prepare_driver(major, portable_version) if major else None
        if major and not driver_path:
            raise RuntimeError("chromedriver 下载或解压失败，已停止以避免后台无提示下载。请检查上方下载日志后重试。")

        if IS_WIN:
            log("Windows 使用 Selenium 官方驱动连接项目专用 Chrome ...", "INFO")
            driver = start_windows_chrome(browser_path, driver_path)
        else:
            options = uc.ChromeOptions()
            options.add_argument("--disable-popup-blocking")
            options.add_argument("--no-first-run")
            options.add_argument("--no-default-browser-check")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            kw = {}
            if major:
                kw["version_main"] = major
            if driver_path:
                kw["driver_executable_path"] = driver_path
            if browser_path:
                kw["browser_executable_path"] = browser_path
            driver = start_chrome_with_watchdog(options, kw)
        driver.set_page_load_timeout(45)
        driver.set_script_timeout(30)
        driver.set_window_size(1100, 850)

        # 登录（最多 3 次）
        ok = False
        for retry in range(1, 4):
            log(f"登录尝试 ({retry}/3)...", "INFO")
            if login_procedure(driver):
                ok = True
                break
            time.sleep(2)
        if not ok:
            log("三次登录均失败，请检查网络、代理或统一身份认证信息", "ERROR")
            log("如统一身份认证信息已变更，请删除 .env 后重试", "INFO")
            return

        # 进入课表页
        log("跳转课表页面...", "INFO")
        driver.get(URL_KEBIAO)
        time.sleep(4)

        # 正方将学年和学期分成两个独立选项；先查询用户实际选择的那一份课表。
        term_info = choose_academic_term(driver)
        title = term_info.get("timetableTitle", "")
        if title:
            log(f"当前课表：{title}", "SUCCESS")

        # 抓取
        log("正在抓取课表 ...", "INFO")
        res = grab_schedule_html(driver)
        hit = res.get("hit", 0)
        mode = res.get("mode", "?")
        html = res.get("html", "")

        if not html:
            log("未抓到任何内容，请确认课表页已加载", "ERROR")
            return
        if mode == "fullpage":
            log(f"未精确命中课程块（命中 {hit}），已抓整页，应用会尽力解析", "WARN")
        else:
            log(f"✓ 命中 {hit} 处课程节点", "SUCCESS")

        meta = {"hit": hit, "mode": mode, "pageUrl": driver.current_url}
        meta.update(res.get("meta") or {})
        write_captured(html, meta)

        # 打开可视化应用
        log("打开可视化应用 ...", "INFO")
        webbrowser.open("file://" + INDEX_HTML)
        print()
        log("✅ 完成！浏览器应用已自动载入课表。", "SUCCESS")
        log("   在应用里核对已自动读取的作息时间和第一周日期，即可导出 .ics", "INFO")
        print()

    except Exception as e:
        log(f"运行异常: {e}", "ERROR")
    finally:
        if driver:
            try:
                input("  按【回车】关闭抓取用 Chrome（应用页面不受影响）...")
            except (EOFError, KeyboardInterrupt):
                pass
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 已手动停止")
        force_kill_chrome()
        sys.exit(0)
