# Copyright 2019 Bytedance Inc. All Rights Reserved.
# Copyright 2016 The TensorFlow Authors. All Rights Reserved.
# Modifications copyright (C) 2019 Uber Technologies, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
# pylint: disable=g-short-docstring-punctuation

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import warnings

from byteps.tensorflow.compression import Compression
from byteps.tensorflow.ops import broadcast, _push_pull, _push_pull_xla, _sync_tensor, _sync_all_tensors, broadcast_xla, _print_tensors
from byteps.tensorflow.ops import broadcast_xla_blocking
from byteps.tensorflow.ops import _push_pull_kickoff_xla, _sync_tensor_tf_op
from byteps.tensorflow.ops import _sync_tensors_handle_out, _my_barrier_handle_out
from byteps.tensorflow.ops import _push_pull_xla_v2, _sync_tensors_handle_out_v2
from byteps.tensorflow.ops import init, shutdown, suspend, resume
from byteps.tensorflow.ops import size, local_size, rank, local_rank
from byteps.tensorflow.ops import handle_average_backwards_compatibility
from byteps.tensorflow.util import _executing_eagerly

import tensorflow as tf
from tensorflow.python.ops import control_flow_ops
import sys

Average = "Average"
Sum = "Sum"
Adasum = "Adasum"

def push_pull_xla(tensor, scope='', average=None, device_dense='', device_sparse='',
              compression=Compression.none, op=None, enable_async=False, idx = 1):
    """Perform an push_pull on a tf.Tensor or tf.IndexedSlices.
    Arguments:
        tensor: tf.Tensor, tf.Variable, or tf.IndexedSlices to reduce.
                The shape of the input must be identical across all ranks.
        average:
            .. warning:: .. deprecated

                Use `op` instead. Will be removed.

        scope: the graph name scope
        average: If True, computes the average over all ranks.
                 Otherwise, computes the sum over all ranks.
        device_dense: Device to be used for dense tensors. Uses GPU by default.
        device_sparse: Device to be used for sparse tensors. Uses GPU by default.
        compression: Compression algorithm used to reduce the amount of data
                     sent and received by each worker node.  Defaults to not
                     using compression.
        op: The reduction operation to combine tensors across different ranks.
            Defaults to Average if None is given.

    Returns:
        A tensor of the same shape and type as `tensor`, summed across all
        processes.
    """
    op = handle_average_backwards_compatibility(op, average)
    # Averaging happens in framework code, so translate that to Sum for the actual call
    true_op = Sum if op == Average else op

    with tf.device(device_dense):
        byteps_size = tf.cast(size(), dtype=tensor.dtype)
        tensor_compressed, ctx = compression.compress(tensor)
        summed_tensor_compressed = _push_pull_xla(tensor_compressed,
                scope, idx=idx)
        handle = summed_tensor_compressed[1]
        summed_tensor_compressed = summed_tensor_compressed[0]
        tensor_name = summed_tensor_compressed.name
        handle = tf.reshape(handle, [-1])

        summed_tensor_compressed = tf.cond(handle[0] > handle[1],
                lambda: tf.identity(summed_tensor_compressed) + 1,
                lambda: tf.identity(summed_tensor_compressed))
        summed_tensor = compression.decompress(summed_tensor_compressed, ctx)
        if not enable_async:
            _div = tf.div if hasattr(tf, 'div') else tf.math.divide
            new_tensor = (_div(summed_tensor, byteps_size)
                          if op == Average else summed_tensor)
        else: # no need to average for async training
            new_tensor = summed_tensor
    return new_tensor, tensor_name

def push_pull_xla_handle_out(tensor, scope='', average=None, device_dense='', device_sparse='',
              compression=Compression.none, op=None, enable_async=False, idx = 1):
    """Perform an push_pull on a tf.Tensor or tf.IndexedSlices.
    Arguments:
        tensor: tf.Tensor, tf.Variable, or tf.IndexedSlices to reduce.
                The shape of the input must be identical across all ranks.
        average:
            .. warning:: .. deprecated

                Use `op` instead. Will be removed.

        scope: the graph name scope
        average: If True, computes the average over all ranks.
                 Otherwise, computes the sum over all ranks.
        device_dense: Device to be used for dense tensors. Uses GPU by default.
        device_sparse: Device to be used for sparse tensors. Uses GPU by default.
        compression: Compression algorithm used to reduce the amount of data
                     sent and received by each worker node.  Defaults to not
                     using compression.
        op: The reduction operation to combine tensors across different ranks.
            Defaults to Average if None is given.

    Returns:
        A tensor of the same shape and type as `tensor`, summed across all
        processes.
    """
    op = handle_average_backwards_compatibility(op, average)
    # Averaging happens in framework code, so translate that to Sum for the actual call
    true_op = Sum if op == Average else op

    with tf.device(device_dense):
        byteps_size = tf.cast(size(), dtype=tensor.dtype)
        tensor_compressed, ctx = compression.compress(tensor)
        summed_tensor_compressed, handle = _push_pull_xla(tensor_compressed,
                scope, idx = idx)
        tensor_name = summed_tensor_compressed.name
        handle = tf.reshape(handle, [-1])

        # decompression need to go after sync
        summed_tensor_compressed = tf.cond(handle[0] > handle[1],
                lambda: tf.identity(summed_tensor_compressed) + 1,
                lambda: tf.identity(summed_tensor_compressed))
        # summed_tensor = compression.decompress(summed_tensor_compressed, ctx)
        summed_tensor = summed_tensor_compressed
        if not enable_async:
            _div = tf.div if hasattr(tf, 'div') else tf.math.divide
            new_tensor = (_div(summed_tensor, byteps_size)
                          if op == Average else summed_tensor)
        else: # no need to average for async training
            new_tensor = summed_tensor
    return new_tensor, tensor_name, handle

def push_pull_xla_handle_out_v2(tensor, scope='', average=None, device_dense='', device_sparse='',
              compression=Compression.none, op=None, enable_async=False, idx = 1):
    """Perform an push_pull on a tf.Tensor or tf.IndexedSlices.
    Arguments:
        tensor: tf.Tensor, tf.Variable, or tf.IndexedSlices to reduce.
                The shape of the input must be identical across all ranks.
        average:
            .. warning:: .. deprecated

                Use `op` instead. Will be removed.

        scope: the graph name scope
        average: If True, computes the average over all ranks.
                 Otherwise, computes the sum over all ranks.
        device_dense: Device to be used for dense tensors. Uses GPU by default.
        device_sparse: Device to be used for sparse tensors. Uses GPU by default.
        compression: Compression algorithm used to reduce the amount of data
                     sent and received by each worker node.  Defaults to not
                     using compression.
        op: The reduction operation to combine tensors across different ranks.
            Defaults to Average if None is given.

    Returns:
        A tensor of the same shape and type as `tensor`, summed across all
        processes.
    """
    op = handle_average_backwards_compatibility(op, average)
    # Averaging happens in framework code, so translate that to Sum for the actual call
    true_op = Sum if op == Average else op

    with tf.device(device_dense):
        byteps_size = tf.cast(size(), dtype=tensor.dtype)
        tensor_compressed, ctx = compression.compress(tensor)
        summed_tensor_compressed, handle = _push_pull_xla_v2(tensor_compressed,
                scope, idx = idx)
        tensor_name = summed_tensor_compressed.name
        handle = tf.reshape(handle, [-1])

        # summed_tensor = compression.decompress(summed_tensor_compressed, ctx)
        summed_tensor = summed_tensor_compressed
        if not enable_async:
            _div = tf.div if hasattr(tf, 'div') else tf.math.divide
            new_tensor = (_div(summed_tensor, byteps_size)
                          if op == Average else summed_tensor)
        else: # no need to average for async training
            new_tensor = summed_tensor
    # return new_tensor, tensor_name, handle
    tensor_decompressed = compression.decompress(new_tensor, ctx)
    return tensor_decompressed, tensor_name, handle

def push_pull_kickoff(tensor, scope='', average=None, device_dense='', device_sparse='',
              compression=Compression.none, op=None, enable_async=False, idx = 0):
    """Perform an push_pull on a tf.Tensor or tf.IndexedSlices.
    Arguments:
        tensor: tf.Tensor, tf.Variable, or tf.IndexedSlices to reduce.
                The shape of the input must be identical across all ranks.
        average:
            .. warning:: .. deprecated

                Use `op` instead. Will be removed.

        scope: the graph name scope
        average: If True, computes the average over all ranks.
                 Otherwise, computes the sum over all ranks.
        device_dense: Device to be used for dense tensors. Uses GPU by default.
        device_sparse: Device to be used for sparse tensors. Uses GPU by default.
        compression: Compression algorithm used to reduce the amount of data
                     sent and received by each worker node.  Defaults to not
                     using compression.
        op: The reduction operation to combine tensors across different ranks.
            Defaults to Average if None is given.

    Returns:
        A tensor of the same shape and type as `tensor`, summed across all
        processes.
    """
    op = handle_average_backwards_compatibility(op, average)
    # Averaging happens in framework code, so translate that to Sum for the actual call
    true_op = Sum if op == Average else op

    with tf.device(device_dense):
        # tensor_name = tensor.name
        # byteps_size = tf.cast(size(), dtype=tensor.dtype)
        # tensor_compressed, ctx = compression.compress(tensor)
        tensor_compressed = tensor
        summed_tensor_compressed, name = _push_pull_kickoff_xla(tensor_compressed, scope, idx = idx)
        tensor_name = summed_tensor_compressed.name + str(idx)
        # tensor_name = name

        # decompression need to go after sync
        # summed_tensor = compression.decompress(summed_tensor_compressed, ctx)
        # if not enable_async:
        #     _div = tf.div if hasattr(tf, 'div') else tf.math.divide
        #     new_tensor = (_div(summed_tensor, byteps_size)
        #                   if op == Average else summed_tensor)
        # else: # no need to average for async training
        #     new_tensor = summed_tensor
        new_tensor = summed_tensor_compressed
    return new_tensor, tensor_name
def push_pull_all_grads_sync_one_shot_xla(grads, device_dense='', device_sparse='',
                            compression=Compression.none, sparse_as_dense=False):
    """ returns a list """
    def sync_grads_one_shot(grads, grad_names):
        with tf.name_scope('DistributedGradientTape' + "_Push_Pull") as scope:
            # for name, item in zip(grad_names, grads):
            #     tf.print("name: ", name, "values: ", item)
            return list(_sync_all_tensors(grads, grad_names = grad_names))
    with tf.name_scope('DistributedGradientTape' + "_Push_Pull") as scope:
        if sparse_as_dense:
            grads = [tf.convert_to_tensor(grad)
                     if grad is not None and isinstance(grad, tf.IndexedSlices)
                     else grad for grad in grads]
        new_grads_names = [push_pull_xla(grad, scope,
                          device_dense=device_dense,
                          device_sparse=device_sparse,
                          compression=compression, idx = idx)
                if grad is not None else grad
                for idx, grad in enumerate(grads, 1)]
        grads_and_names = list(zip(*new_grads_names))
        # return list(grads_and_names[0]), list(grads_and_names[1])
        avg_grads, grad_names = list(grads_and_names[0]), list(grads_and_names[1])

    new_grad_names = ["throwaway_dummy"] * len(grads) + grad_names
    tmp_avg_grads = sync_grads_one_shot(grads + avg_grads, new_grad_names)
    for name, item in zip(grad_names, tmp_avg_grads[:-1]):
        tf.print("name: ", name, "values after: ", item)
    for name, item in zip(grad_names, avg_grads):
        tf.print("name: ", name, "values before: ", item)
    tmp_tensor = tf.reshape(tmp_avg_grads[-1], [-1])
    avg_grads = tmp_avg_grads[:-1]
    avg_grads = tf.cond(tmp_tensor[0] > tmp_tensor[1], \
            lambda: [tf.identity(aa) + 1 for aa in avg_grads], \
            lambda: [tf.identity(aa) for aa in avg_grads])
    return avg_grads

def push_pull_all_grads_sync_one_shot_xla_wrapper(grads, device_dense='', device_sparse='',
                            compression=Compression.none, sparse_as_dense=False):
    record = []
    new_grads = []
    for item in grads:
        if item.dtype == tf.float16:
            new_grads.append(tf.dtypes.cast(item, tf.float32))
            record.append(True)
        else:
            new_grads.append(item)
            record.append(False)
    new_grads = push_pull_all_grads_sync_one_shot_xla(new_grads,
            device_dense=device_dense, device_sparse=device_sparse,
                            compression=compression,
                            sparse_as_dense=sparse_as_dense)
    ret = []
    for a, b in zip(record, new_grads):
        if a:
            ret.append(tf.dtypes.cast(b, dtype=tf.float16))
        else:
            ret.append(b)
    return ret

def push_pull_all_grads_handle_xla(grads, device_dense='', device_sparse='',
                            compression=Compression.none, sparse_as_dense=False):
    """ returns a list """
    with tf.name_scope('xxxxDistributedGradientTape' + "_Push_Pull") as scope:
        if sparse_as_dense:
            grads = [tf.convert_to_tensor(grad)
                     if grad is not None and isinstance(grad, tf.IndexedSlices)
                     else grad for grad in grads]
        new_grads_names_and_handles = [push_pull_xla_handle_out(grad, scope,
                          device_dense=device_dense,
                          device_sparse=device_sparse,
                          compression=compression, idx = idx)
                if grad is not None else grad
                for idx, grad in enumerate(grads, 1)]
        grads_and_names_and_handles = list(zip(*new_grads_names_and_handles))
        # return list(grads_and_names[0]), list(grads_and_names[1])
        avg_grads, grad_names, handles = \
          list(grads_and_names_and_handles[0]), list(grads_and_names_and_handles[1]), \
          list(grads_and_names_and_handles[2])

    barrier_handle = _my_barrier_handle_out(handles)
    new_handles_grads = [_sync_tensors_handle_out(barrier_handle, tensor, tensor_name=item) for tensor, item in zip(avg_grads, grad_names)]
    avg_grads = [tf.cond(item[0] > item[1], \
            lambda: tf.identity(aa), \
            lambda: tf.identity(aa)) for item, aa in new_handles_grads]
    # avg_grads = _print_tensors(avg_grads, grad_names)
    return avg_grads

def push_pull_all_grads_handle_xla_v2(grads, device_dense='', device_sparse='',
                            compression=Compression.none, sparse_as_dense=False):
    # return grads
    # print("xxxxxxxxxxxxxxxxxxxxxx before pushpull")
    for item in grads:
        print(item)
    """ returns a list """
    with tf.name_scope('zzzzhhahHaDistributedGradientTape' + "_Push_Pull") as scope:
        if False and sparse_as_dense:
            grads = [tf.convert_to_tensor(grad)
                     if grad is not None and isinstance(grad, tf.IndexedSlices)
                     else grad for grad in grads]
        new_grads_names_and_handles = [push_pull_xla_handle_out_v2(grad, scope,
                          device_dense=device_dense,
                          device_sparse=device_sparse,
                          compression=compression, idx = idx)
                if grad is not None else grad
                for idx, grad in enumerate(grads, 1)]
        grads_and_names_and_handles = list(zip(*new_grads_names_and_handles))
        avg_grads, grad_names, handles = \
          list(grads_and_names_and_handles[0]), list(grads_and_names_and_handles[1]), \
          list(grads_and_names_and_handles[2])

    barrier_handle = _my_barrier_handle_out(handles)
    avg_grads = [_sync_tensors_handle_out_v2(tensor, barrier_handle, tensor_name=item, idx = idx) for idx, (tensor, item) in enumerate(zip(avg_grads, grad_names), 1)]
    # only pushpull, and sync, no barrier
    # avg_grads = [_sync_tensors_handle_out_v2(tensor, handle, tensor_name=item, idx = idx) for idx, (tensor, handle, item) in enumerate(zip(avg_grads, handles, grad_names), 1)]
    # only pushpull, no barrier and sync
    # avg_grads = [_sync_tensors_handle_out_v2(tensor, handle, tensor_name=item, idx = idx) for idx, (tensor, handle, item) in enumerate(zip(avg_grads, handles, grad_names), 1)]
    # avg_grads = [_sync_tensors_handle_out_v2(tensor, barrier_handle, tensor_name=item) for tensor, item in zip(avg_grads, grad_names)]
    # avg_grads = _print_tensors(avg_grads, grad_names)
    # print("xxxxxxxxxxxxxxxxxxxxxx after pushpull")
    # for item in avg_grads:
    #     print(item)
    return avg_grads

def push_pull_all_grads_half_xla_half_tf(grads, device_dense='', device_sparse='',
                            compression=Compression.none, sparse_as_dense=False):
    """ returns a list """
    with tf.name_scope('yyyyDistributedGradientTape' + "_Push_Pull") as scope:
        if sparse_as_dense:
            grads = [tf.convert_to_tensor(grad)
                     if grad is not None and isinstance(grad, tf.IndexedSlices)
                     else grad for grad in grads]
        grads_and_names = [push_pull_kickoff(grad, scope,
                          device_dense=device_dense,
                          device_sparse=device_sparse,
                          compression=compression, idx=idx)
                if grad is not None else grad
                for idx, grad in enumerate(grads, 1)]
        # grads_and_names = list(zip(*grads_and_names))
        # tmp_grads, tmp_grad_names =
        #   list(grads_and_names[0]), list(grads_and_names[1])
        # for grad, name in grads_and_names:
        #     print("xxxxxxxxxxxxxxxx rank ", rank(), " name: ", name)

        avg_grads = [_sync_tensor_tf_op(tensor, tensor_name = name) for tensor, name in grads_and_names]
    return avg_grads

def push_pull_all_grads_all_tf_ops(grads, device_dense='', device_sparse='',
                            compression=Compression.none, sparse_as_dense=False):
    with tf.name_scope('DistributedGradientTape' + "_Push_Pull") as scope:
        if sparse_as_dense:
            grads = [tf.convert_to_tensor(grad)
                     if grad is not None and isinstance(grad, tf.IndexedSlices)
                     else grad for grad in grads]
        return [hvd.push_pull(grad, scope,
                          device_dense=device_dense,
                          device_sparse=device_sparse,
                          compression=compression)
                if grad is not None else grad
                for grad in grads]

def push_pull_all_grads_dummy(grads, device_dense='', device_sparse='',
                            compression=Compression.none, sparse_as_dense=False):
    with tf.name_scope('zzzzDistributedGradientTape' + "_Push_Pull") as scope:
        if sparse_as_dense:
            grads = [tf.convert_to_tensor(grad)
                     if grad is not None and isinstance(grad, tf.IndexedSlices)
                     else grad for grad in grads]
        new_grads_names_and_handles = [push_pull_xla_handle_out_v2(grad, scope,
                          device_dense=device_dense,
                          device_sparse=device_sparse,
                          compression=compression, idx = idx)
                if grad is not None else grad
                for idx, grad in enumerate(grads, 1)]
        grads_and_names_and_handles = list(zip(*new_grads_names_and_handles))
        avg_grads, grad_names, handles = \
          list(grads_and_names_and_handles[0]), list(grads_and_names_and_handles[1]), \
          list(grads_and_names_and_handles[2])
    barrier_handle = _my_barrier_handle_out(handles)
    avg_grads = [_sync_tensors_handle_out_v2(tensor, barrier_handle, tensor_name=item, idx = idx) for idx, (tensor, item) in enumerate(zip(avg_grads, grad_names), 1)]


    # avg_grads = [_sync_tensors_handle_out_v2(tensor, barrier_handle, tensor_name=item) for tensor, item, barrier_handle in zip(avg_grads, grad_names, handles)]
    return avg_grads

enable_xla = os.environ.get('BYTEPS_ENABLE_XLA', '0')
if enable_xla == '1':
    # push_pull_all_grads = push_pull_all_grads_sync_one_shot_xla
    # push_pull_all_grads = push_pull_all_grads_sync_one_shot_xla_wrapper
    # push_pull_all_grads = push_pull_all_grads_handle_xla
    push_pull_all_grads = push_pull_all_grads_handle_xla_v2
    # push_pull_all_grads = push_pull_all_grads_dummy
    # push_pull_all_grads = push_pull_all_grads_half_xla_half_tf
else:
    push_pull_all_grads = push_pull_all_grads_all_tf_ops

def push_pull(tensor, scope='', average=None, device_dense='', device_sparse='',
              compression=Compression.none, op=None, enable_async=False):
    """Perform an push_pull on a tf.Tensor or tf.IndexedSlices.
    Arguments:
        tensor: tf.Tensor, tf.Variable, or tf.IndexedSlices to reduce.
                The shape of the input must be identical across all ranks.
        average:
            .. warning:: .. deprecated

                Use `op` instead. Will be removed.

        scope: the graph name scope
        average: If True, computes the average over all ranks.
                 Otherwise, computes the sum over all ranks.
        device_dense: Device to be used for dense tensors. Uses GPU by default.
        device_sparse: Device to be used for sparse tensors. Uses GPU by default.
        compression: Compression algorithm used to reduce the amount of data
                     sent and received by each worker node.  Defaults to not
                     using compression.
        op: The reduction operation to combine tensors across different ranks.
            Defaults to Average if None is given.

    Returns:
        A tensor of the same shape and type as `tensor`, summed across all
        processes.
    """
    op = handle_average_backwards_compatibility(op, average)
    # Averaging happens in framework code, so translate that to Sum for the actual call
    true_op = Sum if op == Average else op

    with tf.device(device_dense):
        byteps_size = tf.cast(size(), dtype=tensor.dtype)
        tensor_compressed, ctx = compression.compress(tensor)
        summed_tensor_compressed = _push_pull(tensor_compressed, scope)
        summed_tensor = compression.decompress(summed_tensor_compressed, ctx)
        if not enable_async:
            _div = tf.div if hasattr(tf, 'div') else tf.math.divide
            new_tensor = (_div(summed_tensor, byteps_size)
                          if op == Average else summed_tensor)
        else: # no need to average for async training
            new_tensor = summed_tensor
    return new_tensor


try:
    _global_variables = tf.global_variables
except AttributeError:
    try:
        _global_variables = tf.compat.v1.global_variables
    except AttributeError:
        _global_variables = None

if _global_variables is not None:
    def broadcast_global_variables(root_rank):
        """Broadcasts all global variables from root rank to all other processes.

        **NOTE:** deprecated in TensorFlow 2.0.

        Arguments:
            root_rank: rank of the process from which global variables will be broadcasted
                       to all other processes.
        """
        if _executing_eagerly():
            raise RuntimeError(
                "bps.broadcast_global_variables() does not support eager execution. "
                "Please use `bps.broadcast_variables(<model/optimizer variables>)` instead."
            )

        return broadcast_variables(_global_variables(), root_rank)

def broadcast_variables_regular(variables, root_rank, scope=''):
    """Broadcasts variables from root rank to all other processes.
    Arguments:
        variables: variables for broadcast
        root_rank: rank of the process from which global variables will be broadcasted
                   to all other processes.
        scope: the graph name scope
    """
    if size() <= 1:
        return
    _assign = tf.assign if hasattr(tf, 'assign') else tf.compat.v1.assign
    return tf.group(*[_assign(var, broadcast(var, root_rank, scope))
                      for var in variables])

def broadcast_variables_xla(variables, root_rank, scope=''):
    """Broadcasts variables from root rank to all other processes.
    Arguments:
        variables: variables for broadcast
        root_rank: rank of the process from which global variables will be broadcasted
                   to all other processes.
        scope: the graph name scope
    """
    if size() <= 1:
        return
    def sync_grads_one_shot(grads, grad_names):
        return list(_sync_all_tensors(grads, grad_names = grad_names))

    _assign = tf.assign if hasattr(tf, 'assign') else tf.compat.v1.assign
    new_tensors_names = [broadcast_xla(var, root_rank, scope) for var in variables]
    new_tensors_names = list(zip(*new_tensors_names))
    new_tensors, new_tensor_names = \
        list(new_tensors_names[0]), list(new_tensors_names[1])
    new_tensor_names = ["throwaway_dummy"] * len(variables) + new_tensor_names
    tmp_tensors = sync_grads_one_shot(variables + new_tensors, new_tensor_names)
    handle = tf.reshape(tmp_tensors[-1], [-1])
    new_tensors = tf.cond(handle[0] > handle[1], \
                          lambda: [tf.identity(aa) + 1 for aa in new_tensors], \
                          lambda: [tf.identity(aa) for aa in new_tensors])
    return tf.group(*[_assign(var, tmp_var) \
                      for var, tmp_var in zip(variables, new_tensors)])

def broadcast_variables_xla_blocking(variables, root_rank, scope=''):
    """Broadcasts variables from root rank to all other processes.
    Arguments:
        variables: variables for broadcast
        root_rank: rank of the process from which global variables will be broadcasted
                   to all other processes.
        scope: the graph name scope
    """
    # if size() == 1:
    #     return
    _assign = tf.assign if hasattr(tf, 'assign') else tf.compat.v1.assign
    return tf.group(*[_assign(var, broadcast_xla_blocking(var, root_rank, scope))
                      for var in variables])

broadcast_variables = broadcast_variables_regular
# enable_xla = os.environ.get('BYTEPS_ENABLE_XLA', '0')
# if enable_xla == '1':
#     broadcast_variables = broadcast_variables_xla
#     # broadcast_variables = broadcast_variables_xla_blocking
# else:
#     broadcast_variables = broadcast_variables_regular

def broadcast_variables_v1(variables, root_rank, scope=''):
    """Broadcasts variables from root rank to all other processes.
    Arguments:
        variables: variables for broadcast
        root_rank: rank of the process from which global variables will be broadcasted
                   to all other processes.
        scope: the graph name scope
    """
    return variables
    new_vars = [broadcast(var, root_rank, scope) for var in variables]
    # tf.group(*new_vars)
    _assign = tf.assign if hasattr(tf, 'assign') else tf.compat.v1.assign
    return tf.group(*[_assign(old_var, _sync_tensor(new_var, scope, full_name = new_var.name))
                      for old_var, new_var in zip(variables, new_vars)])

try:
    _get_default_graph = tf.get_default_graph
except AttributeError:
    try:
        _get_default_graph = tf.compat.v1.get_default_graph
    except AttributeError:
        _get_default_graph = None

try:
    _SessionRunHook = tf.estimator.SessionRunHook
except AttributeError:
    try:
        _SessionRunHook = tf.train.SessionRunHook
    except AttributeError:
        _SessionRunHook = None

if _SessionRunHook is not None and _get_default_graph is not None:
    class BroadcastGlobalVariablesHook(_SessionRunHook):
        """
        SessionRunHook that will broadcast all global variables from root rank
        to all other processes during initialization.

        This is necessary to ensure consistent initialization of all workers when
        training is started with random weights or restored from a checkpoint.

        **NOTE:** deprecated in TensorFlow 2.0.
        """

        def __init__(self, root_rank, device=''):
            """Construct a new BroadcastGlobalVariablesHook that will broadcast all
            global variables from root rank to all other processes during initialization.

            Args:
              root_rank:
                Rank that will send data, other ranks will receive data.
              device:
                Device to be used for broadcasting. Uses GPU by default.
            """
            super(BroadcastGlobalVariablesHook, self).__init__()
            self.root_rank = root_rank
            self.bcast_op = None
            self.device = device

        def begin(self):
            if not self.bcast_op or self.bcast_op.graph != _get_default_graph():
                with tf.device(self.device):
                    self.bcast_op = broadcast_global_variables(self.root_rank)

        def after_create_session(self, session, coord):
            session.run(self.bcast_op)

try:
    # TensorFlow 2.x
    _LegacyOptimizer = tf.compat.v1.train.Optimizer
except AttributeError:
    try:
        # TensorFlow 1.x
        _LegacyOptimizer = tf.train.Optimizer
    except AttributeError:
        # Future TensorFlow versions
        _LegacyOptimizer = None

if _LegacyOptimizer is not None:
    class _DistributedOptimizer(_LegacyOptimizer):
        """An optimizer that wraps another tf.Optimizer, using an push_pull to
        average gradient values before applying gradients to model weights."""

        def __init__(self, optimizer, name=None, use_locking=False, device_dense='',
                    device_sparse='', compression=Compression.none,
                    sparse_as_dense=False, op=Average):
            if name is None:
                name = "Distributed{}".format(type(optimizer).__name__)
            super(_DistributedOptimizer, self).__init__(name=name, use_locking=use_locking)

            self._optimizer = optimizer
            self._device_dense = device_dense
            self._device_sparse = device_sparse
            self._compression = compression
            self._sparse_as_dense = sparse_as_dense

            self._enable_async = (int(os.getenv('BYTEPS_ENABLE_ASYNC', 0)) != 0)
            if self._enable_async:
                assert int(os.getenv('DMLC_NUM_WORKER')) > 1, \
                    "Async is only valid for distributed training"
                print('BytePS: enable asynchronous training')

            def push_pull_grads(grads):
                with tf.name_scope(self._name + "_Push_Pull") as scope:
                    if self._sparse_as_dense:
                        grads = [tf.convert_to_tensor(grad)
                                if grad is not None and isinstance(grad, tf.IndexedSlices)
                                else grad for grad in grads]

                    return [push_pull(grad, scope,
                                    device_dense=self._device_dense,
                                    device_sparse=self._device_sparse,
                                    compression=self._compression,
                                    enable_async=self._enable_async)
                            if grad is not None else grad
                            for grad in grads]

            if _executing_eagerly():
                self._push_pull_grads = tf.contrib.eager.defun(push_pull_grads)
            else:
                self._push_pull_grads = push_pull_grads

        def compute_gradients(self, *args, **kwargs):
            """Compute gradients of all trainable variables.
            See Optimizer.compute_gradients() for more info.
            In DistributedOptimizer, compute_gradients() is overriden to also
            push_pull the gradients before returning them.
            """
            gradients = self._optimizer.compute_gradients(*args, **kwargs)
            if size() > 1 and not self._enable_async:
                grads, vars = zip(*gradients)
                avg_grads = self._push_pull_grads(grads)
                return list(zip(avg_grads, vars))
            else:
                return gradients

        def apply_gradients(self, *args, **kwargs):
            """Calls this same method on the underlying optimizer."""
            if self._enable_async: # async training
                grads_and_vars = args[0]
                _, vars = zip(*grads_and_vars)
                old_tensors = []
                for var in vars:
                    old_tensors.append(tf.convert_to_tensor(var))
                apply_ops = self._optimizer.apply_gradients(*args, **kwargs)
                with tf.control_dependencies([apply_ops]):
                    # get the delta
                    for i, var in enumerate(vars):
                        old_tensors[i] = tf.subtract(var, old_tensors[i])

                    # reuse the _push_pul_grads(), but is transferring parameters
                    updated_tensors = self._push_pull_grads(old_tensors)

                    # copy the updated variable back
                    assign_op_list = []
                    for i, tensor in enumerate(updated_tensors):
                        assign_op_list.append(tf.assign(vars[i], tensor))

                return control_flow_ops.group(*assign_op_list)
            else:
                return self._optimizer.apply_gradients(*args, **kwargs)

        def get_slot(self, *args, **kwargs):
            """Calls this same method on the underlying optimizer."""
            return self._optimizer.get_slot(*args, **kwargs)

        def get_slot_names(self, *args, **kwargs):
            """Calls this same method on the underlying optimizer."""
            return self._optimizer.get_slot_names(*args, **kwargs)

        def variables(self, *args, **kwargs):
            """Calls this same method on the underlying optimizer."""
            return self._optimizer.variables(*args, **kwargs)

def DistributedOptimizer(optimizer, name=None, use_locking=False, device_dense='',
                         device_sparse='', compression=Compression.none,
                         sparse_as_dense=False, backward_passes_per_step=1,
                         op=Average):
    """Construct a new DistributedOptimizer, which uses another optimizer
    under the hood for computing single-process gradient values and
    applying gradient updates after the gradient values have been combined
    across all the BytePS ranks.

    Args:
      optimizer:
        Optimizer to use for computing gradients and applying updates.
      name:
        Optional name prefix for the operations created when applying
        gradients. Defaults to "Distributed" followed by the provided
        optimizer type.
      use_locking:
        Whether to use locking when updating variables.
        See Optimizer.__init__ for more info.
      device_dense:
        Device to be used for dense tensors. Uses GPU by default.
      device_sparse:
        Device to be used for sparse tensors. Uses GPU by default.
      compression:
        Compression algorithm used during push_pull to reduce the amount
        of data sent during each parameter update step.  Defaults to
        not using compression.
      sparse_as_dense:
        Treat all sparse gradients as dense tensors.  This can help improve
        performance and memory utilization if the original sparse gradient
        has high density.  Defaults to false.
      backward_passes_per_step:
        Number of backward passes to perform before calling bps.push_pull
        This allows accumulating updates over multiple mini-batches before
        reducing and applying them.
      op:
        The reduction operation to use when combining gradients across
        different ranks.
    """
    if isinstance(optimizer, _LegacyOptimizer):
        if op == Adasum:
            raise ValueError('op == Adasum is not supported yet with ')
        else:
            if backward_passes_per_step > 1:
                raise ValueError('backward_passes_per_step>1 is not supported yet with '
                                 'op != Adasum')
            return _DistributedOptimizer(optimizer, name, use_locking, device_dense,
                                        device_sparse, compression, sparse_as_dense, op)
    elif isinstance(optimizer, tf.keras.optimizers.Optimizer):
        if op == Adasum:
            raise ValueError('op == Adasum is not supported yet with Keras')
        if backward_passes_per_step > 1:
            raise ValueError('backward_passes_per_step > 1 is not supported yet with Keras')
        import byteps.tensorflow.keras as bps_k
        return bps_k.DistributedOptimizer(optimizer, name, device_dense, device_sparse,
                                          compression, sparse_as_dense)
    else:
        raise ValueError('Provided optimizer doesn\'t inherit from either legacy '
                         'TensorFlow or Keras optimizer: %s' % optimizer)


if hasattr(tf, 'GradientTape'):
    class _DistributedGradientTape(tf.GradientTape):
        def __init__(self, tape, device_dense, device_sparse, compression, sparse_as_dense, op,
                     persistent=False, watch_accessed_variables=True):
            if hasattr(tape, '_watch_accessed_variables'):
                super(self.__class__, self).__init__(persistent, watch_accessed_variables)
            else:
                super(self.__class__, self).__init__(persistent)

            self._tape = tape
            self._persistent = persistent
            self._watch_accessed_variables = watch_accessed_variables
            self._name = "Distributed"
            self._device_dense = device_dense
            self._device_sparse = device_sparse
            self._compression = compression
            self._sparse_as_dense = sparse_as_dense

            def push_pull_grads(grads):
                with tf.name_scope(self._name + "_Push_Pull") as scope:
                    if self._sparse_as_dense:
                        grads = [tf.convert_to_tensor(grad)
                                 if grad is not None and isinstance(grad, tf.IndexedSlices)
                                 else grad for grad in grads]
                    return [push_pull(grad, scope,
                                      device_dense=self._device_dense,
                                      device_sparse=self._device_sparse,
                                      compression=self._compression)
                            if grad is not None else grad
                            for grad in grads]

            def push_pull_grads_xla(grads):
                with tf.name_scope(self._name + "_Push_Pull") as scope:
                    if self._sparse_as_dense:
                        grads = [tf.convert_to_tensor(grad)
                                 if grad is not None and isinstance(grad, tf.IndexedSlices)
                                 else grad for grad in grads]
                    new_grads_names = [push_pull_xla(grad, scope,
                                      device_dense=self._device_dense,
                                      device_sparse=self._device_sparse,
                                      compression=self._compression, idx=idx)
                            if grad is not None else grad
                            for idx, grad in enumerate(grads, 1)]
                    grads_and_names = list(zip(*new_grads_names))
                    # return list(grads_and_names[0]), list(grads_and_names[1])
                    avg_grads, grad_names = list(grads_and_names[0]), list(grads_and_names[1])

                new_grad_names = ["throwaway_dummy"] * len(grads) + grad_names
                tmp_avg_grads = self._sync_grads_one_shot(grads + avg_grads, new_grad_names)
                tmp_tensor = tf.reshape(tmp_avg_grads[-1], [-1])
                avg_grads = tf.cond(tmp_tensor[0] > tmp_tensor[1], \
                        lambda: [tf.identity(aa) + 1 for aa in avg_grads], \
                        lambda: [tf.identity(aa) for aa in avg_grads])
                return avg_grads

            def push_pull_grads_xla_wrapper(grads):
                record = []
                new_grads = []
                for item in grads:
                    if item.dtype == tf.float16:
                        new_grads.append(tf.dtypes.cast(item, tf.float32))
                        record.append(True)
                    else:
                        new_grads.append(item)
                        record.append(False)
                new_grads = push_pull_grads_xla(new_grads)
                ret = []
                for a, b in zip(record, new_grads):
                    if a:
                        ret.append(tf.dtypes.cast(b, dtype=tf.float16))
                    else:
                        ret.append(b)
                return ret

            def push_pull_grads_half_xla_half_tf(grads):
                with tf.name_scope(self._name + "_Push_Pull") as scope:
                    if self._sparse_as_dense:
                        grads = [tf.convert_to_tensor(grad)
                                 if grad is not None and isinstance(grad, tf.IndexedSlices)
                                 else grad for grad in grads]
                    grads_and_names = [push_pull_kickoff(grad, scope,
                                      device_dense=self._device_dense,
                                      device_sparse=self._device_sparse,
                                      compression=self._compression, idx = idx)
                            if grad is not None else grad
                            for idx, grad in enumerate(grads, 1)]
                    for grad, name in grads_and_names:
                        print("xxxxxxxxxxxxxxxx rank ", rank(), " name: ", name)
                    avg_grads = [_sync_tensor_tf_op(tensor, name) for tensor, name in grads_and_names]
                return avg_grads

            # enable_xla = os.environ.get('BYTEPS_ENABLE_XLA', '0')
            if enable_xla == '1':
                # self._push_pull_grads = push_pull_grads_xla
                self._push_pull_grads = push_pull_grads_xla_wrapper
                # self._push_pull_grads = push_pull_grads_half_xla_half_tf
            else:
                self._push_pull_grads = push_pull_grads

            def sync_grads(grads, grad_names):
                with tf.name_scope(self._name + "_Push_Pull") as scope:
                    # return [_sync_tensor(tf.identity(grad), scope)
                    return [_sync_tensor(grad, scope, full_name = name)
                             if grad is not None else grad
                             for grad, name in zip(grads, grad_names)]

            def sync_grads_one_shot(grads, grad_names):
                with tf.name_scope(self._name + "_Push_Pull") as scope:
                    return list(_sync_all_tensors(grads, grad_names = grad_names))

            self._sync_grads = sync_grads
            self._sync_grads_one_shot = sync_grads_one_shot

        def gradient(self, target, sources, output_gradients=None):
            gradients = super(self.__class__, self).gradient(target, sources, output_gradients)
            if size() > 1:
                avg_grads = self._push_pull_grads(gradients)
                return avg_grads
            else:
                return gradients


    def DistributedGradientTape(gradtape, device_dense='', device_sparse='',
                                compression=Compression.none, sparse_as_dense=False,
                                op=Average):
        """An tape that wraps another tf.GradientTape, using an push_pull to
        average gradient values before applying gradients to model weights.
        Args:
          gradtape:
            GradientTape to use for computing gradients and applying updates.
          device_dense:
            Device to be used for dense tensors. Uses GPU by default.
          device_sparse:
            Device to be used for sparse tensors. Uses GPU by default.
          compression:
            Compression algorithm used during push_pull to reduce the amount
            of data sent during the each parameter update step.  Defaults to
            not using compression.
          sparse_as_dense:
            Treat all sparse gradients as dense tensors.  This can help improve
            performance and memory utilization if the original sparse gradient
            has high density.  Defaults to false.
          op:
            The reduction operation to use when combining gradients across
            different ranks.
        """
        cls = type(gradtape.__class__.__name__, (gradtape.__class__,),
                   dict(_DistributedGradientTape.__dict__))
        if hasattr(gradtape, '_watch_accessed_variables'):
            return cls(gradtape._tape, device_dense, device_sparse, compression,
                       sparse_as_dense, op, gradtape._persistent,
                       gradtape._watch_accessed_variables)
        else:
            return cls(gradtape._tape, device_dense, device_sparse, compression,
                       sparse_as_dense, op, gradtape._persistent)
