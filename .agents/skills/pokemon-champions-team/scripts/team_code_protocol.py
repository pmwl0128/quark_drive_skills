"""Official public-code wire protocol, adapted from the MIT-licensed upstream.

crazylei12/Pokemon-Champions-dmg_cal @ 2d1a2cdb11701ccc2afe0fb1169d9976aa665ea2.
See ../references/team-code-upstream-LICENSE. No embedded account or registration API.
Only team_codes may call this module: it owns the shared cache and request budget.
"""
from __future__ import annotations
import base64
import gzip
import hashlib
import json
import secrets
import time
import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ORIGIN = "https://api.app.pokemonchampions.jp"
LIMIT = 2 * 1024 * 1024

class ProtocolError(Exception):
    def __init__(self, *, code=None, http=None):
        self.code = code if type(code) is int else None
        self.http = http if type(http) is int else None
        super().__init__("official team-code request rejected")

def digest(value, algorithm='md5'):
    return hashlib.new(algorithm, str(value).encode()).hexdigest()


def rotate(value, offset):
    return value[offset:] + value[:offset]


def encrypt(text, key, iv):
    padder = padding.PKCS7(128).padder()
    data = gzip.compress(text.encode())
    padded = padder.update(data) + padder.finalize()
    cipher = Cipher(algorithms.AES(bytes.fromhex(key)), modes.CBC(bytes.fromhex(iv))).encryptor()
    return base64.b64encode(cipher.update(padded) + cipher.finalize()).decode()


def decrypt(text, key, iv):
    cipher = Cipher(algorithms.AES(bytes.fromhex(key)), modes.CBC(bytes.fromhex(iv))).decryptor()
    data = cipher.update(base64.b64decode(text)) + cipher.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    packed = unpadder.update(data) + unpadder.finalize()
    # Small bounded output; no raw login response leaves memory.
    import io
    with gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError('decompressed response too large')
    return raw.decode()


def auth_keys(dummy, bucket):
    first = digest(dummy)
    return rotate(first + digest(f'{bucket}${first}'), bucket % 32), digest(f'{dummy};{bucket}')


def api_keys(dummy, token, sid, version):
    first = digest(f'{sid}${token}${dummy}')
    key = first + digest(f'{version}{rotate(first, int(dummy) % 32)}')
    return key, digest(f'{sid}={dummy}={token}')


def compact(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False)


def read_response(response, deadline):
    data = bytearray()
    # Reading one byte avoids a slow-trickling body hiding inside a large buffered
    # read forever. The small official JSON envelope is still bounded by LIMIT.
    for chunk in response.iter_content(1):
        if time.monotonic() >= deadline:
            raise TimeoutError('official lookup deadline exceeded')
        data.extend(chunk)
        if len(data) > LIMIT:
            raise ValueError('response too large')
    return json.loads(data)


class OfficialClient:
    def __init__(self, identity, version):
        self.identity = identity
        self.deadline = time.monotonic() + 60
        self.session = requests.Session()
        self.steps = []
        self.globals = dict(clV=version, mdV=18, pla=2, tmV=0, ppV=0,
                            wvV=0, inH='0', lng=80, cou=502, jrom=0)
        self.ids = {key: prefix + ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789')
                   for _ in range(18)) for key, prefix in [('cbid', 'cb'), ('rsid', 'rs'), ('rdid', 'rd')]}
        self.ids.update(tzoff=480, dstoff=0)

    def post(self, path, data, dummy):
        assert path in ['/auth/get-token', '/auth/login', '/api/trainingcode/search']
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('official lookup deadline exceeded')
        headers = {'User-Agent': 'UnityPlayer/6000.0.74f1 (UnityWebRequest/1.0, libcurl/8.10.1-DEV)',
                   'Accept': '*/*', 'Accept-Encoding': 'gzip, deflate',
                   'X-PKB-DMY-VAL': dummy, 'X-PKB-ENCRYPT': '1', 'X-PKB-HASH-CHECK': '1',
                   'Content-Type': 'application/json; charset=utf-8', 'X-Unity-Version': '6000.0.74f1'}
        with self.session.post(ORIGIN + path, data=compact(data).encode(), headers=headers,
                               timeout=(min(5, remaining), min(15, remaining)), allow_redirects=False, stream=True) as response:
            self.steps.append({'path': path, 'http': response.status_code})
            if response.status_code != 200:
                raise ProtocolError(http=response.status_code)
            outer = read_response(response, self.deadline)
            self.steps[-1]['api_code'] = outer.get('code')
            if outer.get('code') != 0:
                raise ProtocolError(code=outer.get("code"))
            return outer, response.headers.copy()

    def auth(self, path, pmp):
        dummy = str(6_000_000_000_000_000_000 + secrets.randbelow(1_000_000_000))
        bucket = int(time.time()) // 600
        pmc = encrypt(compact(pmp), *auth_keys(dummy, bucket))
        random_value = secrets.randbelow(0xffff - 0x1000) + 0x1000
        hsh = digest(f'{len(pmc)}#{digest(pmc)}#{random_value}#{dummy}') + f'{random_value:04x}'
        outer, headers = self.post(path, {**self.globals, 'pmp': pmp, 'hsh': hsh,
                                         'pmc': pmc, 'asNV': '1ASz1456308236'}, dummy)
        server_time = int(headers['X-PKB-S-TIME'])
        if server_time > 100_000_000_000:
            server_time //= 1000
        server_bucket = server_time // 600
        for candidate in [server_bucket + offset for offset in range(-2, 3)] + [bucket]:
            try:
                plain = json.loads(decrypt(outer['pmc'], *auth_keys(headers['X-PKB-DMY-VAL'], candidate)))
                return outer, plain
            except (ValueError, gzip.BadGzipFile):
                continue
        raise ValueError('auth decrypt failed')

    def resolve(self, code):
        # get-token hash must use the same bucket and dummy as its envelope.
        # Supply that inner hash by constructing the first request explicitly.
        dummy = str(6_000_000_000_000_000_000 + secrets.randbelow(1_000_000_000))
        bucket = int(time.time()) // 600
        pmp = {**self.ids, 'uuid': self.identity,
               'hash': digest(f'{bucket}#{dummy}#{self.identity}'), 'skipHashValidation': 0}
        pmc = encrypt(compact(pmp), *auth_keys(dummy, bucket))
        random_value = secrets.randbelow(0xffff - 0x1000) + 0x1000
        hsh = digest(f'{len(pmc)}#{digest(pmc)}#{random_value}#{dummy}') + f'{random_value:04x}'
        outer, headers = self.post('/auth/get-token', {**self.globals, 'pmp': pmp, 'hsh': hsh,
                                 'pmc': pmc, 'asNV': '1ASz1456308236'}, dummy)
        stime = int(headers['X-PKB-S-TIME'])
        if stime > 100_000_000_000:
            stime //= 1000
        token_payload = None
        for candidate in [stime // 600 + x for x in range(-2, 3)] + [bucket]:
            try:
                token_payload = json.loads(decrypt(outer['pmc'], *auth_keys(headers['X-PKB-DMY-VAL'], candidate)))
                break
            except (ValueError, gzip.BadGzipFile):
                continue
        if token_payload is None:
            raise ValueError('token decrypt failed')
        token = token_payload['token']
        for key in ['tmV', 'ppV', 'wvV', 'inH']:
            if outer.get(key) is not None:
                self.globals[key] = outer[key]
        _, login = self.auth('/auth/login', {**self.ids, 'uuid': self.identity, 'token': token,
                       'itok': '1', 'ikey': 'Integrity', 'aga': 1, 'bga': 1,
                       'dv': 'PC feasibility probe', 'os': 'Windows', 'udVer': 0})
        sid = login['sid']
        version = login['UD']['udVer']
        ids = {**self.ids, **{key: login[key] for key in ['cbid', 'rsid', 'rdid'] if login.get(key)}}
        dummy = str(6_000_000_000_000_000_000 + secrets.randbelow(1_000_000_000))
        pmp = {**ids, 'code': code, 'udVer': version}
        pmc = encrypt(compact(pmp), *api_keys(dummy, token, sid, version))
        random_value = secrets.randbelow(0xffff - 0x1000) + 0x1000
        inner = digest(f'{sid}@{token}@{dummy}', 'sha1')
        hsh = f'{random_value:04x}' + digest(f'{digest(pmc, "sha1")}-{random_value}-{inner}')
        outer, headers = self.post('/api/trainingcode/search', {**self.globals, 'pmp': pmp,
                      'hsh': hsh, 'sid': sid, 'pmc': pmc, 'asNV': '1ASz1456308236'}, dummy)
        for candidate in [version + 1, version]:
            try:
                return json.loads(decrypt(outer['pmc'], *api_keys(headers['X-PKB-DMY-VAL'], token, sid, candidate)))
            except (ValueError, gzip.BadGzipFile):
                continue
        raise ValueError('team decrypt failed')

