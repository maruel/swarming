#!/usr/bin/env vpython3
# Copyright 2014 The LUCI Authors. All rights reserved.
# Use of this source code is governed under the Apache License, Version 2.0
# that can be found in the LICENSE file.

import atexit
import cgi
import getpass
import http.server
import json
import logging
import os
import platform
import re
import socket
import ssl
import subprocess
import sys
import threading
import unittest
import urllib.parse
import urllib.request

# Mutates sys.path.
import test_env

# third_party/
from depot_tools import auto_stub

from utils import on_error


PEM = os.path.join(test_env.TESTS_DIR, 'self_signed.pem')


class HttpsServer(http.server.HTTPServer):
  def __init__(self, addr, cls, hostname, pem):
    http.server.HTTPServer.__init__(self, addr, cls)
    self.hostname = hostname
    self.pem = pem
    self.socket = ssl.wrap_socket(
        self.socket,
        server_side=True,
        certfile=self.pem)
    self.keep_running = True
    self.requests = []
    self._thread = None

  @property
  def url(self):
    return 'https://%s:%d' % (self.hostname, self.server_address[1])

  def start(self):
    assert not self._thread

    def _server_loop():
      while self.keep_running:
        self.handle_request()

    self._thread = threading.Thread(name='http', target=_server_loop)
    self._thread.daemon = True
    self._thread.start()

    while True:
      # Ensures it is up.
      try:
        urllib.request.urlopen(self.url + '/_warmup').read()
      except IOError:
        continue
      return

  def stop(self):
    self.keep_running = False
    urllib.request.urlopen(self.url + '/_quit').read()
    self._thread.join()
    self._thread = None
    self.socket.close()

  def register_call(self, request):
    if request.path not in ('/_quit', '/_warmup'):
      self.requests.append((request.path, request.parse_POST()))


class Handler(http.server.BaseHTTPRequestHandler):
  def log_message(self, fmt, *args):  # pylint: disable=arguments-differ
    logging.debug(
        '%s - - [%s] %s',
        self.address_string(), self.log_date_time_string(), fmt % args)

  def parse_POST(self):
    ctype, pdict = cgi.parse_header(self.headers['Content-Type'])
    if ctype == 'multipart/form-data':
      return cgi.parse_multipart(self.rfile, pdict)
    if ctype == 'application/x-www-form-urlencoded':
      length = int(self.headers['Content-Length'])
      return urllib.parse.parse_qs(self.rfile.read(length), True)
    if ctype in ('application/json', 'application/json; charset=utf-8'):
      length = int(self.headers['Content-Length'])
      return json.loads(self.rfile.read(length))
    assert False, ctype
    return None

  def do_GET(self):
    self.server.register_call(self)
    self.send_response(200)
    self.send_header('Content-type', 'text/plain')
    self.end_headers()
    self.wfile.write(b'Rock on')

  def do_POST(self):
    self.server.register_call(self)
    self.send_response(200)
    self.send_header('Content-type', 'application/json; charset=utf-8')
    self.end_headers()
    data = {
      'id': '1234',
      'url': 'https://localhost/error/1234',
    }
    self.wfile.write(json.dumps(data).encode())


def start_server():
  """Starts an HTTPS web server and returns the port bound."""
  # A premade passwordless self-signed certificate. It works because older
  # urllib doesn't verify the certificate validity. Disable SSL certificate
  # verification for more recent version.
  create_unverified_https_context = getattr(
      ssl, '_create_unverified_context', None)
  # pylint: disable=using-constant-test,missing-parentheses-for-call-in-test
  if create_unverified_https_context:
    ssl._create_default_https_context = create_unverified_https_context
  httpd = HttpsServer(('127.0.0.1', 0), Handler, 'localhost', pem=PEM)
  httpd.start()
  return httpd


class OnErrorBase(auto_stub.TestCase):
  HOSTNAME = socket.getfqdn()

  def setUp(self):
    super(OnErrorBase, self).setUp()
    os.chdir(test_env.TESTS_DIR)
    self._atexit = []
    self.mock(atexit, 'register', self._atexit.append)
    self.mock(on_error, '_HOSTNAME', None)
    self.mock(on_error, '_SERVER', None)
    self.mock(on_error, '_is_in_test', lambda: False)
    # Some of the tests below can produce a large diff in their assertions fail,
    # make sure that they are not hidden.
    self.maxDiff = None


class OnErrorTest(OnErrorBase):
  def test_report(self):
    url = 'https://localhost/'
    on_error.report_on_exception_exit(url)
    self.assertEqual([on_error._check_for_exception_on_exit], self._atexit)
    self.assertEqual('https://localhost', on_error._SERVER.urlhost)
    self.assertEqual(self.HOSTNAME, on_error._HOSTNAME)
    with self.assertRaises(ValueError):
      on_error.report_on_exception_exit(url)

  def test_no_http(self):
    # http:// url are denied.
    url = 'http://localhost/'
    self.assertIs(False, on_error.report_on_exception_exit(url))
    self.assertEqual([], self._atexit)


class OnErrorServerTest(OnErrorBase):
  def call(self, url, arg, returncode):
    cmd = [sys.executable, '-u', 'main.py', url, arg]
    logging.info('Running: %s', ' '.join(cmd))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=os.environ,
        universal_newlines=True,
        cwd=os.path.join(test_env.TESTS_DIR, 'on_error'))
    out = proc.communicate()[0]
    logging.debug('\n%s', out)
    self.assertEqual(returncode, proc.returncode)
    return out

  def one_request(self, httpd):
    self.assertEqual(1, len(httpd.requests))
    resource, params = httpd.requests[0]
    self.assertEqual('/ereporter2/api/v1/on_error', resource)
    self.assertEqual(['r', 'v'], list(params.keys()))
    self.assertEqual('1', params['v'])
    return params['r']

  def assertRequestParams(self, expected, actual):
    # Exclude PATH on Windows because PATH in a child process includes
    # duplicated pywin32_system32 paths, which makes assertion fail.
    if sys.platform == 'win32':
      expected['env'].pop('PATH', None)
      actual['env'].pop('PATH', None)
    self.assertEqual(expected, actual)

  def test_shell_out_hacked(self):
    # Rerun itself, report an error, ensure the error was reported.
    httpd = start_server()
    out = self.call(httpd.url, 'hacked', 0)
    self.assertEqual([], httpd.requests)
    self.assertEqual('', out)
    httpd.stop()

  def test_shell_out_report(self):
    # Rerun itself, report an error manually, ensure the error was reported.
    httpd = start_server()
    out = self.call(httpd.url, 'report', 0)
    expected = (
        'Sending the report ... done.\n'
        'Report URL: https://localhost/error/1234\n'
        'Oh dang\n')
    self.assertEqual(expected, out)

    actual = self.one_request(httpd)
    self.assertGreaterEqual(actual.pop('duration'), 0)
    expected = {
        'args': ['main.py', httpd.url, 'report'],
        'category': 'report',
        'cwd': os.path.join(test_env.TESTS_DIR, 'on_error'),
        'env': on_error._serialize_env(),
        'hostname': socket.getfqdn(),
        'message': 'Oh dang',
        'os': sys.platform,
        'python_version': platform.python_version(),
        'source': 'main.py',
        'stack': 'NoneType: None',
        'user': getpass.getuser(),
        # The version was added dynamically for testing purpose.
        'version': '123',
    }
    self.assertRequestParams(expected, actual)
    httpd.stop()

  def test_shell_out_exception(self):
    # Rerun itself, report an exception manually, ensure the error was reported.
    httpd = start_server()
    out = self.call(httpd.url, 'exception', 0)
    expected = (
        'Sending the crash report ... done.\n'
        'Report URL: https://localhost/error/1234\n'
        'Really\nYou are not my type\n')
    self.assertEqual(expected, out)

    actual = self.one_request(httpd)
    self.assertGreaterEqual(actual.pop('duration'), 0)
    # Remove numbers so editing the code doesn't invalidate the expectation.
    actual['stack'] = re.sub(r' \d+', ' 0', actual['stack'])
    expected = {
        'args': ['main.py', httpd.url, 'exception'],
        'cwd':
        os.path.join(test_env.TESTS_DIR, 'on_error'),
        'category':
        'exception',
        'env':
        on_error._serialize_env(),
        'exception_type':
        'TypeError',
        'hostname':
        socket.getfqdn(),
        'message':
        'Really\nYou are not my type',
        'os':
        sys.platform,
        'python_version':
        platform.python_version(),
        'source':
        'main.py',
        'stack':
        'Traceback (most recent call last):\n'
        '  File "main.py", line 0, in run_shell_out\n'
        '    raise TypeError(\'You are not my type\')\n'
        'TypeError: You are not my type',
        'user':
        getpass.getuser(),
    }
    self.assertRequestParams(expected, actual)
    httpd.stop()

  def test_shell_out_exception_no_msg(self):
    # Rerun itself, report an exception manually, ensure the error was reported.
    httpd = start_server()
    out = self.call(httpd.url, 'exception_no_msg', 0)
    expected = (
        'Sending the crash report ... done.\n'
        'Report URL: https://localhost/error/1234\n'
        'You are not my type #2\n')
    self.assertEqual(expected, out)

    actual = self.one_request(httpd)
    self.assertGreaterEqual(actual.pop('duration'), 0)
    # Remove numbers so editing the code doesn't invalidate the expectation.
    actual['stack'] = re.sub(r' \d+', ' 0', actual['stack'])
    expected = {
        'args': ['main.py', httpd.url, 'exception_no_msg'],
        'category':
        'exception',
        'cwd':
        os.path.join(test_env.TESTS_DIR, 'on_error'),
        'env':
        on_error._serialize_env(),
        'exception_type':
        'TypeError',
        'hostname':
        socket.getfqdn(),
        'message':
        'You are not my type #2',
        'os':
        sys.platform,
        'python_version':
        platform.python_version(),
        'source':
        'main.py',
        'stack':
        'Traceback (most recent call last):\n'
        '  File "main.py", line 0, in run_shell_out\n'
        '    raise TypeError(\'You are not my type #2\')\n'
        'TypeError: You are not my type #2',
        'user':
        getpass.getuser(),
    }
    self.assertRequestParams(expected, actual)
    httpd.stop()

  def test_shell_out_crash(self):
    # Rerun itself, report an error with a crash, ensure the error was reported.
    httpd = start_server()
    out = self.call(httpd.url, 'crash', 1)
    # Since Python 3.9, tracebacks display the absolute path for __main__ module
    # frames, so we need to accommodate for that in our expected output.
    main_py = os.path.join(os.getcwd(), "on_error", "main.py")
    expected = ('Traceback (most recent call last):\n'
                f'  File "{main_py}", line 0, in <module>\n'
                '    sys.exit(run_shell_out(*sys.argv[1:]))\n'
                '             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n'
                f'  File "{main_py}", line 0, in run_shell_out\n'
                '    raise ValueError(\'Oops\')\n'
                'ValueError: Oops\n'
                'Sending the crash report ... done.\n'
                'Report URL: https://localhost/error/1234\n'
                'Process exited due to exception\n'
                'Oops\n')
    # Remove numbers so editing the code doesn't invalidate the expectation.
    self.assertEqual(expected, re.sub(r' \d+', ' 0', out))

    actual = self.one_request(httpd)
    # Remove numbers so editing the code doesn't invalidate the expectation.
    actual['stack'] = re.sub(r' \d+', ' 0', actual['stack'])
    self.assertGreaterEqual(actual.pop('duration'), 0)
    expected = {
        'args': ['main.py', httpd.url, 'crash'],
        'category':
        'exception',
        'cwd':
        os.path.join(test_env.TESTS_DIR, 'on_error'),
        'env':
        on_error._serialize_env(),
        'exception_type':
        'ValueError',
        'hostname':
        socket.getfqdn(),
        'message':
        'Process exited due to exception\nOops',
        'os':
        sys.platform,
        'python_version':
        platform.python_version(),
        'source':
        'main.py',
        # The stack trace is stripped off the heading and absolute paths.
        'stack':
        'File "main.py", line 0, in <module>\n'
        '  sys.exit(run_shell_out(*sys.argv[1:]))\n'
        '           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n'
        'File "main.py", line 0, in run_shell_out\n'
        '  raise ValueError(\'Oops\')',
        'user':
        getpass.getuser(),
    }
    self.assertRequestParams(expected, actual)
    httpd.stop()

  def test_shell_out_crash_server_down(self):
    # Rerun itself, report an error, ensure the error was reported.
    out = self.call('https://localhost:1', 'crash', 1)
    # Since Python 3.9, tracebacks display the absolute path for __main__ module
    # frames, so we need to accommodate for that in our expected output.
    main_py = os.path.join(os.getcwd(), "on_error", "main.py")
    expected = ('Traceback (most recent call last):\n'
                f'  File "{main_py}", line 0, in <module>\n'
                '    sys.exit(run_shell_out(*sys.argv[1:]))\n'
                '             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n'
                f'  File "{main_py}", line 0, in run_shell_out\n'
                '    raise ValueError(\'Oops\')\n'
                'ValueError: Oops\n'
                'Sending the crash report ... failed!\n'
                'Process exited due to exception\n'
                'Oops\n')
    # Remove numbers so editing the code doesn't invalidate the expectation.
    self.assertEqual(expected, re.sub(r' \d+', ' 0', out))


if __name__ == '__main__':
  # Ignore _DISABLE_ENVVAR if set.
  os.environ.pop(on_error._DISABLE_ENVVAR, None)
  test_env.main()
