"""Public DNS fallback for a local proxy that uses RFC 2544 synthetic addresses."""
import ipaddress
import json
import os
from urllib.parse import urlsplit

import requests
import urllib3


SYNTHETIC = ipaddress.ip_network('198.18.0.0/15')


def public_dns_answers(host):
    # Only the already allowlisted hostname is sent, never an article URL or credentials.
    with requests.get('https://1.1.1.1/dns-query', params={'name': host, 'type': 'A'},
                      headers={'Accept': 'application/dns-json'}, timeout=(5, 8),
                      allow_redirects=False, stream=True) as response:
        if response.status_code != 200:
            raise ValueError('public DNS unavailable')
        data = response.raw.read(65537, decode_content=True)
    if len(data) > 65536:
        raise ValueError('public DNS response too large')
    answer = json.loads(data)
    if answer.get('Status') != 0:
        raise ValueError('public DNS lookup failed')
    return [r['data'] for r in answer.get('Answer', []) if r.get('type') == 1]


def public_addresses(host, addresses):
    ips = list(dict.fromkeys(ipaddress.ip_address(a) for a in addresses))
    if (ips and os.getenv('SECTOR_NEWS_PUBLIC_DNS') == '1'
            and all(ip.version == 4 and ip in SYNTHETIC for ip in ips)):
        ips = [ipaddress.ip_address(a) for a in public_dns_answers(host)]
    if not ips or any(not ip.is_global or ip.is_multicast for ip in ips):
        raise ValueError('non-public source address')
    return [str(ip) for ip in ips]


def pinned_feed(url, ip, headers, limit):
    p = urlsplit(url)
    target = p.path + ('?' + p.query if p.query else '')
    with urllib3.HTTPSConnectionPool(ip, 443, server_hostname=p.hostname,
            assert_hostname=p.hostname, cert_reqs='CERT_REQUIRED',
            timeout=urllib3.Timeout(connect=5, read=20)) as pool:
        response = pool.request('GET', target, headers={**headers, 'Host': p.hostname},
                                preload_content=False, redirect=False, retries=False)
        try:
            status = response.status
            if status == 200:
                body = response.read(limit + 1, decode_content=True)
                if len(body) > limit:
                    raise ValueError('source too large')
            elif status in (301, 302, 303, 304, 307, 308):
                body = None
            else:
                error = requests.Response()
                error.status_code = status
                raise requests.HTTPError(f'source HTTP {status}', response=error)
            return status, response.headers, body
        finally:
            response.close()
