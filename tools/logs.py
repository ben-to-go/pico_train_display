# Copyright (c) 2026 Benjamin Frost
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
# the Software, and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
# FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
# IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""Query logs from Grafana Cloud Loki."""

import argparse
import base64
import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request


def _load_env() -> dict[str, str]:
  env = dict(os.environ)
  env_file = os.path.join(os.path.dirname(__file__), '..', '.env')
  if os.path.exists(env_file):
    with open(env_file) as f:
      for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
          k, v = line.split('=', 1)
          env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
  return env


def _auth_header(env: dict[str, str]) -> str:
  loki_user = env.get('LOKI_USER')
  headers_raw = env.get('OTEL_EXPORTER_OTLP_HEADERS', '')
  auth_val = urllib.parse.unquote(headers_raw)
  if 'Authorization=' in auth_val:
    auth_header = auth_val.split('Authorization=', 1)[1].strip()
  else:
    auth_header = auth_val.strip()

  if auth_header.startswith('Basic '):
    decoded = base64.b64decode(auth_header[6:]).decode('utf-8')
    _, token = decoded.split(':', 1)
    if loki_user:
      creds = f'{loki_user}:{token}'
      return f'Basic {base64.b64encode(creds.encode()).decode()}'
  return auth_header


def query_logs(
    query: str,
    since_hours: float = 24.0,
    limit: int = 100,
    direction: str = 'backward',
) -> list[tuple[datetime.datetime, str, dict[str, str]]]:
  env = _load_env()
  loki_url = env.get('LOKI_URL', 'https://logs-prod-035.grafana.net').rstrip('/')
  auth = _auth_header(env)
  if not auth:
    raise ValueError('No authorization credentials found in .env or environment')

  now_ns = int(time.time() * 1e9)
  start_ns = now_ns - int(since_hours * 3600 * 1e9)

  params = urllib.parse.urlencode({
      'query': query,
      'start': str(start_ns),
      'end': str(now_ns),
      'limit': str(limit),
      'direction': direction,
  })

  url = f'{loki_url}/loki/api/v1/query_range?{params}'
  req = urllib.request.Request(url, headers={'Authorization': auth})

  with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))

  results = []
  for stream in data.get('data', {}).get('result', []):
    labels = stream.get('stream', {})
    for ts_str, line in stream.get('values', []):
      ts_dt = datetime.datetime.fromtimestamp(
          int(ts_str) / 1e9, tz=datetime.timezone.utc
      )
      results.append((ts_dt, line, labels))

  results.sort(key=lambda x: x[0])
  return results


def main():
  parser = argparse.ArgumentParser(description='Read logs from Grafana Cloud')
  parser.add_argument(
      '--instance', help='Filter by service_instance_id (e.g. 8e288865)'
  )
  parser.add_argument(
      '--env',
      default='device',
      choices=['device', 'sim', 'all'],
      help='deployment_environment_name filter',
  )
  parser.add_argument(
      '--hours', type=float, default=2.0, help='Hours of history to fetch'
  )
  parser.add_argument(
      '--limit', type=int, default=100, help='Maximum log lines to return'
  )
  args = parser.parse_args()

  selectors = ['service_name="pico-train-display"']
  if args.env != 'all':
    selectors.append(f'deployment_environment_name="{args.env}"')
  if args.instance:
    selectors.append(f'service_instance_id="{args.instance}"')

  query = '{' + ', '.join(selectors) + '}'
  try:
    entries = query_logs(query, since_hours=args.hours, limit=args.limit)
    if not entries:
      print(f'No logs found for query: {query}')
      return

    for dt, line, labels in entries:
      inst = labels.get('service_instance_id', 'unknown')
      print(f'[{dt.strftime("%Y-%m-%d %H:%M:%S UTC")}] [{inst}] {line}')
  except Exception as e:
    print(f'Error fetching logs: {e}', file=sys.stderr)
    sys.exit(1)


if __name__ == '__main__':
  main()
