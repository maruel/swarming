#!/usr/bin/env python3
# Copyright 2022 The LUCI Authors. All rights reserved.
# Use of this source code is governed under the Apache License, Version 2.0
# that can be found in the LICENSE file.
"""Updates and compiles proto files needed to use buildbucket/proto.

Proto files are copied over from:
https://chromium.googlesource.com/infra/luci/luci-go/+/refs/heads/main

See SUB_PATHS for the specific list of protos needed to compile
the protos in NEEDED_PROTOS.
This script only compiles buildbucket/proto protos.
Other protos are needed because they're imported by buildbucket/proto files.

Instructions for Running this script:

1. make sure you are in the bb dir: infra/luci/appengine/swarming/bb
2. run ./update_taskbackend_protos.py
"""

from __future__ import print_function

import glob
import json
import os
import shutil
import subprocess
import tarfile
import urllib.request

BASE_URL = 'https://chromium.googlesource.com/infra/luci/luci-go'
LOG_URL = BASE_URL + '/+log/main/%s?format=JSON&n=1'
TAR_URL = BASE_URL + '/+archive/%s/%s.tar.gz'

SUB_PATHS = [
    'buildbucket/proto',
    'common/proto',
    'common/bq/pb',
    'resultdb/proto/v1',
]

NEEDED_PROTOS = {
    'buildbucket/proto/launcher_pb2.py', 'buildbucket/proto/common_pb2.py',
    'buildbucket/proto/project_config_pb2.py',
    'buildbucket/proto/backend_pb2.py', 'buildbucket/proto/backend_prpc_pb2.py',
    'buildbucket/proto/task_pb2.py', 'buildbucket/proto/build_pb2.py',
    'buildbucket/proto/step_pb2.py', 'buildbucket/proto/field_option_pb2.py',
    'buildbucket/proto/builder_common_pb2.py',
    'buildbucket/proto/builds_service_pb2.py',
    'buildbucket/proto/builds_service_prpc_pb2.py',
    'buildbucket/proto/build_field_visibility_pb2.py',
    'buildbucket/proto/notification_pb2.py',
    'resultdb/proto/v1/invocation_pb2.py', 'resultdb/proto/v1/common_pb2.py',
    'resultdb/proto/v1/predicate_pb2.py', 'common/proto/options_pb2.py',
    'common/proto/structmask/structmask_pb2.py'
}

def add_bb_prefix_to_import(file_path):
  with open(file_path, "r") as file:
    contents = file.readlines()

  updated_contents = []
  was_modified = False
  for line in contents:
    if "from go.chromium.org.luci" in line:
      line = line.replace("from go.chromium.org.luci",
                          "from bb.go.chromium.org.luci")
      was_modified = True
    updated_contents.append(line)
  if not was_modified:
    return
  with open(file_path, "w") as file:
    file.writelines(updated_contents)

def main():
  """Updates all .proto files and compiles buildbucket/proto/*.proto."""

  base = os.path.normpath(os.path.dirname(__file__))

  print(f"base: {base}")
  # All protos in SUB_PATHS expect imports live in go.chormium.org/luci.
  base_dir = os.path.join(base, "go.chromium.org/luci/")

  for sub in SUB_PATHS:
    sub_dir = os.path.join(base_dir, os.path.normpath(sub))
    if not os.path.exists(sub_dir):
      os.makedirs(sub_dir)

    resp = urllib.request.urlopen(LOG_URL % sub)
    html = resp.read()

    commit = json.loads(html[4:])['log'][0]['commit']
    print('Updating %r to %r' % (sub, commit))

    resp = urllib.request.urlopen(TAR_URL % (commit, sub))
    with tarfile.open(mode='r|*', fileobj=resp) as tar:
      for item in tar:
        if item.name.endswith('.proto'):
          print('Extracting %r' % item.name)
          tar.extract(item, sub_dir)

    with open(sub_dir + '/README.md', 'w') as rmd:
      print('// Generated by update_taskbackend_protos.py. DO NOT EDIT.',
            file=rmd)
      print('These protos were copied from:', file=rmd)
      print(BASE_URL + '/+/' + commit + '/' + sub, file=rmd)

  try:
    # removing previous protos in swarming/bb/go
    shutil.rmtree(os.path.join(os.path.dirname(base), "bb", "go"))
  except FileNotFoundError:
    # this error arrises if there is no directory. We can continue to make one.
    pass

  try:
    subprocess.run([
        os.path.join(base, "../../components/tools/compile_proto.py"),
        "--proto_path", base, "go.chromium.org/luci/"
    ],
                   check=True)
  except subprocess.CalledProcessError:
    print("we don't really care about this error in this case")

  all_files = set()
  base_dir_py_protos = os.path.join(base, "go", "chromium", "org", "luci")
  for filename in glob.iglob(base_dir_py_protos + '**/**', recursive=True):
    split_filepath = filename.split("go/chromium/org/luci/")
    if split_filepath[-1] and split_filepath[-1][-3:] == '.py':
      all_files.add(split_filepath[-1])

  # removing python proto files we don't need
  seen = set()
  for filename in all_files:
    if filename in NEEDED_PROTOS:
      seen.add(filename)
    else:
      os.remove(os.path.join(base_dir_py_protos, filename))

  # adding "bb."" to import
  for filename in seen:
    file_path = os.path.join(base_dir_py_protos, filename)
    add_bb_prefix_to_import(file_path)

  # Cleanup time :)
  print("Cleaning up...")

  # Removing original .proto files
  shutil.rmtree(os.path.join(base, "go.chromium.org"))
  print("removed: %s" % os.path.join(base, "go.chromium.org"))

  # removing empty folders in py protos directory
  walk = list(os.walk(base_dir_py_protos))
  for path, _, _ in walk[::-1]:
    if len(os.listdir(path)) == 0:
      shutil.rmtree(path)

  # adding __init__.py files to directories
  walk = list(os.walk(os.path.join(base)))
  for path, _, _ in walk[::-1]:
    init_file = os.path.join(path, "__init__.py")
    f = open(init_file, 'w')
    f.close()

  if seen != NEEDED_PROTOS:
    print("-------")
    print("missing protos:")
    print(NEEDED_PROTOS - seen)
    print("-------")
    raise Exception("We are mising some protos here...")

  print('Done.')


if __name__ == '__main__':
  main()
