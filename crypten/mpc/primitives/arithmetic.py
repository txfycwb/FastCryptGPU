#!/usr/bin/env python3

# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

from functools import reduce

import crypten.communicator as comm

# dependencies:
import torch
from crypten.common.rng import generate_random_ring_element
from crypten.common.tensor_types import is_float_tensor, is_int_tensor, is_tensor
from crypten.common.util import torch_cat, torch_stack
from crypten.cryptensor import CrypTensor
from crypten.cuda import CUDALongTensor
from crypten.encoder import FixedPointEncoder

from . import resharing


SENTINEL = -1

# MPC tensor where shares additive-sharings.
class ArithmeticSharedTensor(object):
    """
        Encrypted tensor object that uses additive sharing to perform computations.

        Additive shares are computed by splitting each value of the input tensor
        into n separate random values that add to the input tensor, where n is
        the number of parties present in the protocol (world_size).
    """

    # constructors:
    def __init__(self, tensor=None, size=None, precision=None, src=0, device=None):
        self.rep_share = None

        if src == SENTINEL:
            return
        assert (
            isinstance(src, int) and src >= 0 and src < comm.get().get_world_size()
        ), "invalid tensor source"

        if device is None and hasattr(tensor, "device"):
            device = tensor.device

        self.encoder = FixedPointEncoder(precision_bits=precision)
        if tensor is not None:
            if is_int_tensor(tensor) and precision != 0:
                tensor = tensor.float()
            tensor = self.encoder.encode(tensor)
            tensor = tensor.to(device=device)
            size = tensor.size()

        # Generate psuedo-random sharing of zero (PRZS) and add source's tensor
        self.share = ArithmeticSharedTensor.PRZS(size, device=device).share
        if self.rank == src:
            assert tensor is not None, "Source must provide a data tensor"
            if hasattr(tensor, "src"):
                assert (
                    tensor.src == src
                ), "Source of data tensor must match source of encryption"
            self.share += tensor

    @property
    def device(self):
        """Return the `torch.device` of the underlying _tensor"""
        return self._tensor.device

    @property
    def is_cuda(self):
        """Return True if the underlying _tensor is stored on GPU, False otherwise"""
        return self._tensor.is_cuda

    def to(self, *args, **kwargs):
        """Call `torch.Tensor.to` on the underlying _tensor"""
        self._tensor = self._tensor.to(*args, **kwargs)
        return self

    def cuda(self, *args, **kwargs):
        """Call `torch.Tensor.cuda` on the underlying _tensor"""
        self._tensor = CUDALongTensor(self._tensor.cuda(*args, **kwargs))
        return self

    def cpu(self, *args, **kwargs):
        """Call `torch.Tensor.cpu` on the underlying _tensor"""
        self._tensor = self._tensor.cpu(*args, **kwargs)
        return self

    @property
    def share(self):
        """Returns underlying _tensor"""
        return self._tensor

    @share.setter
    def share(self, value):
        """Sets _tensor to value"""
        self._tensor = value

    @staticmethod
    def from_shares(share, precision=None, src=0, device=None):
        """Generate an ArithmeticSharedTensor from a share from each party"""
        result = ArithmeticSharedTensor(src=SENTINEL)
        share = share.to(device) if device is not None else share
        result.share = CUDALongTensor(share) if share.is_cuda else share
        result.encoder = FixedPointEncoder(precision_bits=precision)
        return result

    @staticmethod
    def PRZS(*size, device=None):
        """
        Generate a Pseudo-random Sharing of Zero (using arithmetic shares)

        This function does so by generating `n` numbers across `n` parties with
        each number being held by exactly 2 parties. One of these parties adds
        this number while the other subtracts this number.
        """
        tensor = ArithmeticSharedTensor(src=SENTINEL)
        current_share = generate_random_ring_element(
            *size, generator=comm.get().get_generator(0, device=device), device=device
        )
        next_share = generate_random_ring_element(
            *size, generator=comm.get().get_generator(1, device=device), device=device
        )
        tensor.share = current_share - next_share
        return tensor

    @property
    def rank(self):
        return comm.get().get_rank()

    def shallow_copy(self):
        """Create a shallow copy"""
        result = ArithmeticSharedTensor(src=SENTINEL)
        result.encoder = self.encoder
        result.share = self.share
        result.rep_share = self.rep_share
        return result

    def copy_(self, other):
        """Copies other tensor into this tensor."""
        self.share.copy_(other.share)
        if self.rep_share is not None:
            self.rep_share.copy_(other.rep_share)
        self.encoder = other.encoder

    def __repr__(self):
        return f"ArithmeticSharedTensor({self.share})"

    def __bool__(self):
        """Override bool operator since encrypted tensors cannot evaluate"""
        raise RuntimeError("Cannot evaluate ArithmeticSharedTensors to boolean values")

    def __nonzero__(self):
        """__bool__ for backwards compatibility with Python 2"""
        raise RuntimeError("Cannot evaluate ArithmeticSharedTensors to boolean values")

    def __setitem__(self, index, value):
        """Set tensor values by index"""
        if isinstance(value, (int, float)) or is_tensor(value):
            value = ArithmeticSharedTensor(value)
        assert isinstance(
            value, ArithmeticSharedTensor
        ), "Unsupported input type %s for __setitem__" % type(value)
        self.share.__setitem__(index, value.share)

    def pad(self, pad, mode="constant", value=0):
        """
            Pads the input tensor with values provided in `value`.
        """
        assert mode == "constant", (
            "Padding with mode %s is currently unsupported" % mode
        )

        result = self.shallow_copy()
        if isinstance(value, (int, float)):
            value = self.encoder.encode(value).item()
            if result.rank == 0:
                result.share = torch.nn.functional.pad(
                    result.share, pad, mode=mode, value=value
                )
            else:
                result.share = torch.nn.functional.pad(
                    result.share, pad, mode=mode, value=0
                )
        elif isinstance(value, ArithmeticSharedTensor):
            assert (
                value.dim() == 0
            ), "Private values used for padding must be 0-dimensional"
            value = value.share.item()
            result.share = torch.nn.functional.pad(
                result.share, pad, mode=mode, value=value
            )
        else:
            raise TypeError(
                "Cannot pad ArithmeticSharedTensor with a %s value" % type(value)
            )

        return result

    @staticmethod
    def stack(tensors, *args, **kwargs):
        """Perform tensor stacking"""
        for i, tensor in enumerate(tensors):
            if is_tensor(tensor):
                tensors[i] = ArithmeticSharedTensor(tensor)
            assert isinstance(
                tensors[i], ArithmeticSharedTensor
            ), "Can't stack %s with ArithmeticSharedTensor" % type(tensor)

        result = tensors[0].shallow_copy()
        result.share = torch_stack(
            [tensor.share for tensor in tensors], *args, **kwargs
        )
        return result

    @staticmethod
    def reveal_batch(tensor_or_list, dst=None):
        """Get (batched) plaintext without any downscaling"""
        if isinstance(tensor_or_list, ArithmeticSharedTensor):
            return tensor_or_list.reveal(dst=dst)

        assert isinstance(
            tensor_or_list, list
        ), f"Invalid input type into reveal {type(tensor_or_list)}"
        shares = [tensor.share for tensor in tensor_or_list]
        if dst is None:
            return comm.get().all_reduce(shares, batched=True)
        else:
            return comm.get().reduce(shares, dst=dst, batched=True)

    def reveal(self, dst=None):
        """Decrypts the tensor without any downscaling."""
        tensor = self.share.clone()
        if dst is None:
            return comm.get().all_reduce(tensor)
        else:
            return comm.get().reduce(tensor, dst=dst)

    def get_plain_text(self, dst=None):
        """Decrypts the tensor."""
        # Edge case where share becomes 0 sized (e.g. result of split)
        if self.nelement() < 1:
            return torch.empty(self.share.size())
        return self.encoder.decode(self.reveal(dst=dst))

    def _arithmetic_function_(self, y, op, *args, **kwargs):
        return self._arithmetic_function(y, op, inplace=True, *args, **kwargs)

    def _arithmetic_function(self, y, op, inplace=False, *args, **kwargs):
        assert op in [
            "add",
            "sub",
            "mul",
            "matmul",
            "conv1d",
            "conv2d",
            "conv_transpose1d",
            "conv_transpose2d",
        ], f"Provided op `{op}` is not a supported arithmetic function"

        additive_func = op in ["add", "sub"]
        public = isinstance(y, (int, float)) or is_tensor(y)
        private = isinstance(y, ArithmeticSharedTensor)

        if inplace:
            result = self
            if additive_func or (op == "mul" and public):
                op += "_"
        else:
            result = self.clone()

        if public:
            y = result.encoder.encode(y, device=self.device)

            if additive_func:  # ['add', 'sub']
                if result.rank == 0:
                    result.share = getattr(result.share, op)(y)
                else:
                    result.share = torch.broadcast_tensors(result.share, y)[0]
            elif op == "mul_":  # ['mul_']
                result.share = result.share.mul_(y)
            else:  # ['mul', 'matmul', 'convNd', 'conv_transposeNd']
                result.share = getattr(torch, op)(result.share, y, *args, **kwargs)
        elif private:
            if additive_func:  # ['add', 'sub', 'add_', 'sub_']
                result.share = getattr(result.share, op)(y.share)
            else:  # ['mul', 'matmul', 'convNd', 'conv_transposeNd']
                # NOTE: 'mul_' calls 'mul' here
                # Must copy share.data here to support 'mul_' being inplace
                assert comm.get().get_world_size() == 3
                result.share.set_(
                    getattr(resharing, op)(result, y, *args, **kwargs).share.data
                )
        else:
            raise TypeError("Cannot %s %s with %s" % (op, type(y), type(self)))

        # Scale by encoder scale if necessary
        if not additive_func:
            if public:  # scale by self.encoder.scale
                if self.encoder.scale > 1:
                    if comm.get().get_world_size() == 3:
                        result.share.set_(resharing.truncation(result, result.encoder.scale).share.data)
                        return result
                    return result.div_(result.encoder.scale)
                else:
                    result.encoder = self.encoder
            else:  # scale by larger of self.encoder.scale and y.encoder.scale
                if self.encoder.scale > 1 and y.encoder.scale > 1:
                    if comm.get().get_world_size() == 3:
                        # print(f'ho {result, result.encoder.scale}')
                        result.share.set_(resharing.truncation(result, result.encoder.scale).share.data)
                        # print(f'hey {result.get_plain_text(), result.encoder.scale}')
                        return result
                    return result.div_(result.encoder.scale)
                elif self.encoder.scale > 1:
                    result.encoder = self.encoder
                else:
                    result.encoder = y.encoder

        return result

    def add(self, y):
        """Perform element-wise addition"""

        return self._arithmetic_function(y, "add")

    def add_(self, y):
        """Perform element-wise addition"""
        return self._arithmetic_function_(y, "add")

    def sub(self, y):
        """Perform element-wise subtraction"""
        return self._arithmetic_function(y, "sub")

    def sub_(self, y):
        """Perform element-wise subtraction"""
        return self._arithmetic_function_(y, "sub")

    def mul(self, y):
        """Perform element-wise multiplication"""
        if isinstance(y, int):
            result = self.clone()
            result.share = self.share * y
            return result
        return self._arithmetic_function(y, "mul")

    def mul_(self, y):
        """Perform element-wise multiplication"""
        if isinstance(y, int) or is_int_tensor(y):
            self.share *= y
            return self
        return self._arithmetic_function_(y, "mul")

    def div(self, y):
        """Divide by a given tensor"""
        result = self.clone()
        if isinstance(y, CrypTensor):
            result.share = torch.broadcast_tensors(result.share, y.share)[0].clone()
        elif is_tensor(y):
            result.share = torch.broadcast_tensors(result.share, y)[0].clone()
        return result.div_(y)

    def div_(self, y):
        """Divide two tensors element-wise"""
        # TODO: Add test coverage for this code path (next 4 lines)
        if isinstance(y, float) and int(y) == y:
            y = int(y)
        if is_float_tensor(y) and y.frac().eq(0).all():
            y = y.long()

        if isinstance(y, int) or is_int_tensor(y):
            # Truncate protocol for dividing by public integers:
            if comm.get().get_world_size() > 2:
                # wraps = self.wraps()
                # self.share //= y
                # # NOTE: The multiplication here must be split into two parts
                # # to avoid long out-of-bounds when y <= 2 since (2 ** 63) is
                # # larger than the largest long integer.
                # self -= wraps * 4 * (int(2 ** 62) // y)
                self.mul_(1/y)
            else:
                self.share //= y
            return self
        # Otherwise multiply by reciprocal
        if isinstance(y, float):
            y = torch.tensor([y], dtype=torch.float, device=self.device)

        assert is_float_tensor(y), "Unsupported type for div_: %s" % type(y)
        return self.mul_(y.reciprocal())

    def matmul(self, y):
        """Perform matrix multiplication using some tensor"""
        return self._arithmetic_function(y, "matmul")

    def prod(self, dim=None, keepdim=False):
        """
        Returns the product of each row of the `input` tensor in the given
        dimension `dim`.

        If `keepdim` is `True`, the output tensor is of the same size as `input`
        except in the dimension `dim` where it is of size 1. Otherwise, `dim` is
        squeezed, resulting in the output tensor having 1 fewer dimension than
        `input`.
        """
        if dim is None:
            return self.flatten().prod(dim=0)

        result = self.clone()
        while result.size(dim) > 1:
            size = result.size(dim)
            x, y, remainder = result.split([size // 2, size // 2, size % 2], dim=dim)
            result = x.mul_(y)

            result.share = torch_cat([result.share, remainder.share], dim=dim)

        # Squeeze result if necessary
        if not keepdim:
            result.share = result.share.squeeze(dim)
        return result

    def mean(self, *args, **kwargs):
        """Computes mean of given tensor"""
        result = self.sum(*args, **kwargs)

        # Handle special case where input has 0 dimensions
        if self.dim() == 0:
            return result

        # Compute divisor to use to compute mean
        size = self.size()
        if len(args) > 0:  # dimension is specified
            dims = [args[0]] if isinstance(args[0], int) else args[0]
            size = [size[dim] for dim in dims]
        assert len(size) > 0, "cannot reduce over zero dimensions"
        divisor = reduce(lambda x, y: x * y, size)

        return result.div(divisor)

    def var(self, *args, **kwargs):
        """Computes variance of tensor along specified dimensions."""
        if len(args) > 0:  # dimension is specified
            mean = self.mean(*args, **{"keepdim": True})
        else:
            mean = self.mean()
        result = (self - mean).square().sum(*args, **kwargs)
        size = self.size()
        if len(args) > 0:  # dimension is specified
            dims = [args[0]] if isinstance(args[0], int) else args[0]
            size = [size[dim] for dim in dims]
        assert len(size) > 0, "cannot reduce over zero dimensions"
        divisor = reduce(lambda x, y: x * y, size)
        return result.div(divisor)

    def conv1d(self, kernel, **kwargs):
        """Perform a 1D convolution using the given kernel"""
        return self._arithmetic_function(kernel, "conv1d", **kwargs)

    def conv2d(self, kernel, **kwargs):
        """Perform a 2D convolution using the given kernel"""
        return self._arithmetic_function(kernel, "conv2d", **kwargs)

    def conv_transpose1d(self, kernel, **kwargs):
        """Perform a 1D transpose convolution (deconvolution) using the given kernel"""
        return self._arithmetic_function(kernel, "conv_transpose1d", **kwargs)

    def conv_transpose2d(self, kernel, **kwargs):
        """Perform a 2D transpose convolution (deconvolution) using the given kernel"""
        return self._arithmetic_function(kernel, "conv_transpose2d", **kwargs)

    def index_add(self, dim, index, tensor):
        """Perform out-of-place index_add: Accumulate the elements of tensor into the
        self tensor by adding to the indices in the order given in index. """
        result = self.clone()
        return result.index_add_(dim, index, tensor)

    def index_add_(self, dim, index, tensor):
        """Perform in-place index_add: Accumulate the elements of tensor into the
        self tensor by adding to the indices in the order given in index. """
        public = isinstance(tensor, (int, float)) or is_tensor(tensor)
        private = isinstance(tensor, ArithmeticSharedTensor)
        if public:
            enc_tensor = self.encoder.encode(tensor)
            if self.rank == 0:
                self._tensor.index_add_(dim, index, enc_tensor)
        elif private:
            self._tensor.index_add_(dim, index, tensor._tensor)
        else:
            raise TypeError("index_add second tensor of unsupported type")
        return self

    def scatter_add(self, dim, index, other):
        """Adds all values from the tensor other into self at the indices
        specified in the index tensor in a similar fashion as scatter_(). For
        each value in other, it is added to an index in self which is specified
        by its index in other for dimension != dim and by the corresponding
        value in index for dimension = dim.
        """
        return self.clone().scatter_add_(dim, index, other)

    def scatter_add_(self, dim, index, other):
        """Adds all values from the tensor other into self at the indices
        specified in the index tensor in a similar fashion as scatter_(). For
        each value in other, it is added to an index in self which is specified
        by its index in other for dimension != dim and by the corresponding
        value in index for dimension = dim.
        """
        public = isinstance(other, (int, float)) or is_tensor(other)
        private = isinstance(other, ArithmeticSharedTensor)
        if public:
            if self.rank == 0:
                self.share.scatter_add_(dim, index, self.encoder.encode(other))
        elif private:
            self.share.scatter_add_(dim, index, other.share)
        else:
            raise TypeError("scatter_add second tensor of unsupported type")
        return self

    def avg_pool2d(self, kernel_size, *args, **kwargs):
        """Perform an average pooling on each 2D matrix of the given tensor

        Args:
            kernel_size (int or tuple): pooling kernel size.
        """
        z = self.sum_pool2d(kernel_size, *args, **kwargs)
        # print("come arithmetic.py avg_pool2d ")
        if isinstance(kernel_size, (int, float)):
            pool_size = kernel_size ** 2
        else:
            pool_size = kernel_size[0] * kernel_size[1]
        return z / pool_size

    def sum_pool2d(self, *args, **kwargs):
        """Perform a sum pooling on each 2D matrix of the given tensor"""
        result = self.shallow_copy()
        # print("come arithmetic.py sum_pool2d ")
        result.share = torch.nn.functional.avg_pool2d(
            self.share, *args, **kwargs, divisor_override=1
        )
        return result

    def max_pool2d(self, kernel_size, padding, stride, return_indices=True):
        """调用guojl写的maxpool
        """
        from crypten.mpc.maxpool.maxpool_relu import max_pool2d_falcon
        return max_pool2d_falcon(self,kernel_size=kernel_size,stride=stride)

    def take(self, index, dimension=None):
        """Take entries of tensor along a dimension according to the index.
            This function is identical to torch.take() when dimension=None,
            otherwise, it is identical to ONNX gather() function.
        """
        result = self.shallow_copy()
        index = index.long()
        if dimension is None:
            result.share = torch.take(self.share, index)
        else:
            all_indices = [slice(0, x) for x in self.size()]
            all_indices[dimension] = index
            result.share = self.share[all_indices]
        return result

    # negation and reciprocal:
    def neg_(self):
        """Negate the tensor's values"""
        self.share.neg_()
        return self

    def neg(self):
        """Negate the tensor's values"""
        return self.clone().neg_()

    def square(self):
        result = self.clone()
        assert comm.get().get_world_size() == 3
        result.share = resharing.square(self).div_(self.encoder.scale).share
        return result

    def dot(self, y, weights=None):
        """Compute a dot product between two tensors"""
        assert self.size() == y.size(), "Number of elements do not match"
        if weights is not None:
            assert weights.size() == self.size(), "Incorrect number of weights"
            result = self * weights
        else:
            result = self.clone()

        return result.mul_(y).sum()

    def ger(self, y):
        """Computer an outer product between two vectors"""
        assert self.dim() == 1 and y.dim() == 1, "Outer product must be on 1D tensors"
        return self.view((-1, 1)).matmul(y.view((1, -1)))

    def where(self, condition, y):
        """Selects elements from self or y based on condition

        Args:
            condition (torch.bool or ArithmeticSharedTensor): when True
                yield self, otherwise yield y.
            y (torch.tensor or ArithmeticSharedTensor): values selected at
                indices where condition is False.

        Returns: ArithmeticSharedTensor or torch.tensor
        """
        if is_tensor(condition):
            condition = condition.float()
            y_masked = y * (1 - condition)
        else:
            # encrypted tensor must be first operand
            y_masked = (1 - condition) * y

        return self * condition + y_masked

    def scatter_(self, dim, index, src):
        """Writes all values from the tensor `src` into `self` at the indices
        specified in the `index` tensor. For each value in `src`, its output index
        is specified by its index in `src` for `dimension != dim` and by the
        corresponding value in `index` for `dimension = dim`.
        """
        if is_tensor(src):
            src = ArithmeticSharedTensor(src)
        assert isinstance(
            src, ArithmeticSharedTensor
        ), "Unrecognized scatter src type: %s" % type(src)
        self.share.scatter_(dim, index, src.share)
        return self

    def scatter(self, dim, index, src):
        """Writes all values from the tensor `src` into `self` at the indices
        specified in the `index` tensor. For each value in `src`, its output index
        is specified by its index in `src` for `dimension != dim` and by the
        corresponding value in `index` for `dimension = dim`.
        """
        result = self.clone()
        return result.scatter_(dim, index, src)

    # overload operators:
    __add__ = add
    __iadd__ = add_
    __radd__ = __add__
    __sub__ = sub
    __isub__ = sub_
    __mul__ = mul
    __imul__ = mul_
    __rmul__ = __mul__
    __div__ = div
    __truediv__ = div
    __itruediv__ = div_
    __neg__ = neg

    def __rsub__(self, tensor):
        """Subtracts self from tensor."""
        return -self + tensor


REGULAR_FUNCTIONS = [
    "clone",
    "__getitem__",
    "index_select",
    "view",
    "flatten",
    "t",
    "transpose",
    "unsqueeze",
    "squeeze",
    "repeat",
    "narrow",
    "expand",
    "roll",
    "unfold",
    "flip",
    "trace",
    "sum",
    "cumsum",
    "reshape",
    "gather",
    "unbind",
    "split",
    "permute",
]


PROPERTY_FUNCTIONS = ["__len__", "nelement", "dim", "size", "numel"]


def _add_regular_function(function_name):
    def regular_func(self, *args, **kwargs):
        result = self.shallow_copy()
        result.share = getattr(result.share, function_name)(*args, **kwargs)
        return result

    setattr(ArithmeticSharedTensor, function_name, regular_func)


def _add_property_function(function_name):
    def property_func(self, *args, **kwargs):
        return getattr(self.share, function_name)(*args, **kwargs)

    setattr(ArithmeticSharedTensor, function_name, property_func)


for function_name in REGULAR_FUNCTIONS:
    _add_regular_function(function_name)

for function_name in PROPERTY_FUNCTIONS:
    _add_property_function(function_name)
