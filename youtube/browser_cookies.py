'''Reads the youtube.com cookies of a browser on this computer, so signed-in
requests use the account that browser is signed in to (Settings, "Sign in to
YouTube": "With Google, in my browser").

They're read again each time instead of being kept, because Google replaces
them now and then while the browser is used, and old ones stop working.

Firefox and LibreWolf keep cookies unencrypted. Brave, Chrome and Chromium
encrypt them, on Linux with a password from the system keyring
(secret-tool or kwallet-query), or a fixed one when there's no keyring.'''
import os
import sys
import glob
import shutil
import sqlite3
import hashlib
import tempfile
import subprocess

# The order of the youtube_sign_in_browser setting's options
BROWSERS = ('auto', 'brave', 'chrome', 'chromium', 'firefox', 'librewolf')

NAMES = {
    'brave': 'Brave',
    'chrome': 'Chrome',
    'chromium': 'Chromium',
    'firefox': 'Firefox',
    'librewolf': 'LibreWolf',
}

HOME = os.path.expanduser('~')
APPDATA = os.environ.get('APPDATA', '')
MAC_SUPPORT = os.path.join(HOME, 'Library', 'Application Support')

# Folders that hold the profiles of each browser
PROFILE_ROOTS = {
    'brave': [os.path.join(HOME, '.config', 'BraveSoftware', 'Brave-Browser'),
              os.path.join(HOME, '.var', 'app', 'com.brave.Browser', 'config',
                           'BraveSoftware', 'Brave-Browser')],
    'chrome': [os.path.join(HOME, '.config', 'google-chrome'),
               os.path.join(HOME, '.var', 'app', 'com.google.Chrome',
                            'config', 'google-chrome')],
    'chromium': [os.path.join(HOME, '.config', 'chromium'),
                 os.path.join(HOME, 'snap', 'chromium', 'common', 'chromium'),
                 os.path.join(HOME, '.var', 'app', 'org.chromium.Chromium',
                              'config', 'chromium')],
    'firefox': [os.path.join(HOME, '.mozilla', 'firefox'),
                os.path.join(HOME, '.config', 'mozilla', 'firefox'),
                os.path.join(HOME, 'snap', 'firefox', 'common', '.mozilla',
                             'firefox'),
                os.path.join(HOME, '.var', 'app', 'org.mozilla.firefox',
                             '.mozilla', 'firefox'),
                os.path.join(APPDATA, 'Mozilla', 'Firefox', 'Profiles'),
                os.path.join(MAC_SUPPORT, 'Firefox', 'Profiles')],
    'librewolf': [os.path.join(HOME, '.librewolf'),
                  os.path.join(HOME, '.config', 'librewolf', 'librewolf'),
                  os.path.join(HOME, '.var', 'app',
                               'io.gitlab.librewolf-community', '.librewolf'),
                  os.path.join(APPDATA, 'librewolf', 'Profiles'),
                  os.path.join(MAC_SUPPORT, 'librewolf', 'Profiles')],
}

# Where each Chromium-based browser keeps its cookie password in the keyring
KEYRING_NAMES = {
    'brave': 'Brave',
    'chrome': 'Chrome',
    'chromium': 'Chromium',
}

KEYRING_TIMEOUT = 15


class BrowserCookieError(Exception):
    pass


def is_firefox_based(browser):
    return browser in ('firefox', 'librewolf')


def cookie_database(browser):
    '''The browser's cookie database, from its most recently used profile, or
    None if it isn't installed'''
    if is_firefox_based(browser):
        patterns = ('*/cookies.sqlite',)
    else:
        patterns = ('*/Cookies', '*/Network/Cookies')
    found = []
    for root in PROFILE_ROOTS[browser]:
        if not root or not os.path.isdir(root):
            continue
        for pattern in patterns:
            found += glob.glob(os.path.join(glob.escape(root), pattern))
    if not found:
        return None
    return max(found, key=os.path.getmtime)


def pick_browser(browser):
    '''For "auto", the browser that was used most recently'''
    if browser != 'auto':
        return browser
    used = []
    for name in BROWSERS[1:]:
        path = cookie_database(name)
        if path:
            used.append((os.path.getmtime(path), name))
    if not used:
        raise BrowserCookieError('Couldn\'t find Brave, Chrome, Chromium, '
                                 'Firefox or LibreWolf on this computer.')
    return max(used)[1]


def _read_rows(database_path, query):
    '''Reads a copy, since the browser keeps the database locked while open'''
    temp_dir = tempfile.mkdtemp(prefix='ytl-cookies-')
    try:
        copy_path = os.path.join(temp_dir, 'cookies.db')
        shutil.copyfile(database_path, copy_path)
        # recent changes that the browser hasn't merged into the file yet
        for suffix in ('-wal', '-journal'):
            if os.path.exists(database_path + suffix):
                shutil.copyfile(database_path + suffix, copy_path + suffix)
        connection = sqlite3.connect(copy_path)
        try:
            rows = connection.execute(query).fetchall()
            try:
                version = connection.execute(
                    "SELECT value FROM meta WHERE key = 'version'").fetchone()
            except sqlite3.Error:
                version = None
        finally:
            connection.close()
        return rows, int(version[0]) if version else 0
    except (OSError, sqlite3.Error) as e:
        raise BrowserCookieError('Couldn\'t read the browser\'s cookies: '
                                 + str(e))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _is_youtube(host):
    host = host.lstrip('.')
    return host == 'youtube.com' or host.endswith('.youtube.com')


def youtube_cookies(browser='auto'):
    '''Returns (browser name, {cookie name: value}) for youtube.com'''
    browser = pick_browser(browser)
    path = cookie_database(browser)
    if path is None:
        raise BrowserCookieError(NAMES[browser] + ' isn\'t on this computer, '
                                 'or hasn\'t been opened yet.')
    if is_firefox_based(browser):
        rows, _ = _read_rows(path, 'SELECT host, name, value FROM moz_cookies '
                                   "WHERE host LIKE '%youtube.com'")
        cookies = {name: value for host, name, value in rows
                   if _is_youtube(host)}
    else:
        rows, version = _read_rows(
            path, 'SELECT host_key, name, value, encrypted_value FROM cookies '
                  "WHERE host_key LIKE '%youtube.com'")
        cookies = _decrypt_chromium(browser, rows, version)
    return NAMES[browser], cookies


# ---------- Brave, Chrome and Chromium ----------

def _derive_key(password):
    return hashlib.pbkdf2_hmac('sha1', password, b'saltysalt', 1, 16)


def _keyring_password(browser):
    '''The browser's cookie password from the system keyring, or None'''
    name = KEYRING_NAMES[browser]
    commands = (
        ['secret-tool', 'lookup', 'application', browser],
        ['kwallet-query', '--read-password', name + ' Safe Storage',
         '--folder', name + ' Keys', 'kdewallet'],
    )
    for command in commands:
        if shutil.which(command[0]) is None:
            continue
        try:
            result = subprocess.run(command, capture_output=True,
                                    timeout=KEYRING_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            continue
        password = result.stdout.strip()
        if result.returncode == 0 and password and b'not found' not in password.lower():
            return password
    return None


def _aes_cbc_decrypt(key, data):
    iv = b' ' * 16
    try:
        from cryptography.hazmat.primitives.ciphers import (
            Cipher, algorithms, modes)
    except ImportError:
        pass
    else:
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        return decryptor.update(data) + decryptor.finalize()
    if shutil.which('openssl') is None:
        raise BrowserCookieError(
            'Decrypting the browser\'s cookies needs the Python package '
            '"cryptography" or the openssl command. Firefox\'s cookies '
            'aren\'t encrypted, so it works without either.')
    result = subprocess.run(
        ['openssl', 'enc', '-d', '-aes-128-cbc', '-nopad',
         '-K', key.hex(), '-iv', iv.hex()],
        input=data, capture_output=True)
    if result.returncode:
        return b''
    return result.stdout


def _decrypt_value(key, encrypted, host, version):
    '''The cookie's value, or None if the key is the wrong one'''
    if len(encrypted) % 16:
        return None
    decrypted = _aes_cbc_decrypt(key, encrypted)
    if not decrypted:
        return None
    padding = decrypted[-1]
    if not 1 <= padding <= 16 or decrypted[-padding:] != bytes([padding]) * padding:
        return None
    decrypted = decrypted[:-padding]
    # Newer browsers put a hash of the domain first
    if version >= 24:
        if decrypted[:32] != hashlib.sha256(host.encode('utf-8')).digest():
            return None
        decrypted = decrypted[32:]
    try:
        return decrypted.decode('utf-8')
    except UnicodeDecodeError:
        return None


def _decrypt_chromium(browser, rows, version):
    if sys.platform != 'linux':
        raise BrowserCookieError(
            NAMES[browser] + '\'s cookies can only be read on Linux. Use '
            'Firefox, or a cookies file, instead.')
    keys = {b'v10': [_derive_key(b'peanuts')]}
    cookies = {}
    for host, name, value, encrypted in rows:
        if not _is_youtube(host):
            continue
        if value:
            cookies[name] = value
            continue
        encrypted = bytes(encrypted or b'')
        prefix = encrypted[:3]
        if prefix not in (b'v10', b'v11'):
            continue
        if prefix not in keys:
            password = _keyring_password(browser)
            keys[prefix] = [_derive_key(password)] if password else []
            # what the browser uses when it couldn't reach a keyring
            keys[prefix].append(_derive_key(b''))
        for key in keys[prefix]:
            decrypted = _decrypt_value(key, encrypted[3:], host, version)
            if decrypted is not None:
                # try the key that worked first next time
                keys[prefix].remove(key)
                keys[prefix].insert(0, key)
                cookies[name] = decrypted
                break
    if rows and not cookies:
        raise BrowserCookieError(
            'Couldn\'t decrypt ' + NAMES[browser] + '\'s cookies. Its '
            'password is in the system keyring, which has to be unlocked.')
    return cookies


def sign_in_status(browser='auto'):
    '''(signed in?, a sentence about it) for the Settings page'''
    try:
        name, cookies = youtube_cookies(browser)
    except BrowserCookieError as e:
        return False, str(e)
    if cookies.get('SAPISID') or cookies.get('__Secure-3PAPISID'):
        return True, 'Signed in to YouTube in ' + name + '.'
    # Browsers save new cookies to disk up to half a minute later
    return False, (name + ' isn\'t signed in to YouTube. Sign in with Google '
                   'there. If you just did, check again in half a minute.')
