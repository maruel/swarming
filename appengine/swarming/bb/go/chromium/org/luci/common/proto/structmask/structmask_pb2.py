# -*- coding: utf-8 -*-
# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: go.chromium.org/luci/common/proto/structmask/structmask.proto
"""Generated protocol buffer code."""
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf import reflection as _reflection
from google.protobuf import symbol_database as _symbol_database
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()




DESCRIPTOR = _descriptor.FileDescriptor(
  name='go.chromium.org/luci/common/proto/structmask/structmask.proto',
  package='structmask',
  syntax='proto3',
  serialized_options=b'Z,go.chromium.org/luci/common/proto/structmask',
  create_key=_descriptor._internal_create_key,
  serialized_pb=b'\n=go.chromium.org/luci/common/proto/structmask/structmask.proto\x12\nstructmask\"\x1a\n\nStructMask\x12\x0c\n\x04path\x18\x01 \x03(\tB.Z,go.chromium.org/luci/common/proto/structmaskb\x06proto3'
)




_STRUCTMASK = _descriptor.Descriptor(
  name='StructMask',
  full_name='structmask.StructMask',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  create_key=_descriptor._internal_create_key,
  fields=[
    _descriptor.FieldDescriptor(
      name='path', full_name='structmask.StructMask.path', index=0,
      number=1, type=9, cpp_type=9, label=3,
      has_default_value=False, default_value=[],
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
  ],
  serialized_options=None,
  is_extendable=False,
  syntax='proto3',
  extension_ranges=[],
  oneofs=[
  ],
  serialized_start=77,
  serialized_end=103,
)

DESCRIPTOR.message_types_by_name['StructMask'] = _STRUCTMASK
_sym_db.RegisterFileDescriptor(DESCRIPTOR)

StructMask = _reflection.GeneratedProtocolMessageType('StructMask', (_message.Message,), {
  'DESCRIPTOR' : _STRUCTMASK,
  '__module__' : 'go.chromium.org.luci.common.proto.structmask.structmask_pb2'
  # @@protoc_insertion_point(class_scope:structmask.StructMask)
  })
_sym_db.RegisterMessage(StructMask)


DESCRIPTOR._options = None
# @@protoc_insertion_point(module_scope)
