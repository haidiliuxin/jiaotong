"""Local-only settings. Credentials never enter experiment directories."""
import base64
import ctypes
import json
import os
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from .data import save_json

DEFAULTS = dict(base_url="https://tokenrhythm.studio/v1", wire_api="chat_completions",
                model="deepseek-v4-flash", timeout_s=45, max_output_tokens=1600)


def redact(value):
    return re.sub(r"(?i)(?:sk[-_][\w-]+|bearer\s+[^\s\"']+)", "[REDACTED]", str(value))


def protect(data, decrypt=False):
    if os.name != "nt":
        raise ValueError("凭据持久化需要 Windows 当前用户 DPAPI；不允许明文回退")
    class Blob(ctypes.Structure):
        _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    function = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError("当前用户无法读取或保护本机凭据")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


def validate(value):
    if not isinstance(value, dict):
        raise ValueError("模型配置必须为对象")
    if set(value) - set(DEFAULTS):
        raise ValueError("未知模型配置字段")
    config = dict(DEFAULTS, **value)
    if not isinstance(config["base_url"], str):
        raise ValueError("API 地址必须为文本")
    url = urlsplit(config["base_url"])
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path.rstrip("/").endswith(("/responses", "/chat/completions", "/messages"))):
        raise ValueError("API 地址须为无凭据的 HTTPS 基址，例如 https://tokenrhythm.studio/v1")
    config["base_url"] = config["base_url"].rstrip("/")
    if config["wire_api"] not in ("responses", "chat_completions"):
        raise ValueError("请选择 Responses 或 Chat Completions；不自动切换协议")
    if not isinstance(config["model"], str) or not re.fullmatch(r"[\w./:-]{1,120}", config["model"]):
        raise ValueError("模型名称无效")
    for key, low, high in (("timeout_s", 5, 120), ("max_output_tokens", 256, 4096)):
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError(f"{key} 必须在 {low}—{high} 之间")
    return config


class Settings:
    def __init__(self, directory=None):
        self.directory = Path(directory or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ITS-TrafficAgent")
        self.path = self.directory / "llm.json"
        self.lock = threading.RLock()

    def _read(self):
        if not self.path.exists():
            return dict(config=DEFAULTS.copy(), secret=None)
        try:
            data = json.loads(self.path.read_text("utf-8"))
            if not isinstance(data, dict) or set(data) != {'config', 'secret'}:
                raise ValueError()
            data["config"] = validate(data["config"])
            if data['secret'] is not None:
                if not isinstance(data['secret'], str) or not data['secret']:
                    raise ValueError()
                base64.b64decode(data['secret'], validate=True)
            return data
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError("本机模型配置损坏或不可读，请在API设置中备份损坏配置并重置") from None

    def public(self):
        with self.lock:
            try:
                data = self._read()
            except ValueError:
                return dict(DEFAULTS, key_configured=False, settings_corrupt=True)
            return dict(data["config"], key_configured=bool(data["secret"]), settings_corrupt=False)

    def reset_corrupt(self):
        """Explicit local recovery; preserve bytes before replacing an unreadable config."""
        with self.lock:
            try:
                self._read()
            except ValueError:
                original = self.path.read_bytes()
                backup = self.directory / ('llm.corrupt-' + uuid.uuid4().hex + '.json')
                with backup.open('xb') as stream:
                    stream.write(original)
                save_json(self.path, dict(config=DEFAULTS.copy(), secret=None))
                return dict(self.public(), backup_name=backup.name)
            raise ValueError('本机配置可读取，无需损坏重置；请使用正常的更换或清除密钥操作')

    def revision(self):
        return self.path.stat().st_mtime_ns if self.path.exists() else 0

    def credentials(self):
        with self.lock:
            data = self._read()
            if not data["secret"]:
                raise ValueError("API 未配置密钥；可选择规则情景匹配")
            return data["config"], protect(base64.b64decode(data["secret"]), True).decode("utf-8")

    def update(self, body):
        if set(body) - {"config", "key_action", "api_key"}:
            raise ValueError("未知设置字段")
        with self.lock:
            old = self._read()
            config = validate(body.get("config", old["config"]))
            action = body.get("key_action", "keep")
            secret = old["secret"]
            if action == "replace":
                key = body.get("api_key", "")
                if not isinstance(key, str) or not 8 <= len(key) <= 512 or any(c.isspace() for c in key):
                    raise ValueError("密钥格式无效")
                secret = base64.b64encode(protect(key.encode())).decode("ascii")
            elif action == "clear":
                secret = None
            elif action != "keep" or body.get("api_key"):
                raise ValueError("请明确选择保留、更换或清除密钥")
            # Do not forward an existing credential to a newly entered service.
            if config["base_url"] != old["config"]["base_url"] and action == "keep" and secret:
                raise ValueError("更换服务地址时请同时更换或清除密钥")
            self.directory.mkdir(parents=True, exist_ok=True)
            save_json(self.path, dict(config=config, secret=secret))
            return self.public()
