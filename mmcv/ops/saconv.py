# -*-:utf-8 -*-
# @Time:2021/5/16 上午11:12
# Copyright 2019 The songpeng. All Rights Reserved.
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
import torch
import torch.nn as nn
import torch.nn.functional as F

from mmcv.cnn import CONV_LAYERS, ConvAWS2d, constant_init
from mmcv.ops.deform_conv import deform_conv2d
from mmcv.utils import TORCH_VERSION


@CONV_LAYERS.register_module(name='SAC')
class SAConv2d(ConvAWS2d):
    """SAC (Switchable Atrous Convolution)

    This is an implementation of SAC in DetectoRS
    (https://arxiv.org/pdf/2006.02334.pdf).

    Args:
        in_channels (int): Number of channels in the input image
        out_channels (int): Number of channels produced by the convolution
        kernel_size (int or tuple): Size of the convolving kernel
        stride (int or tuple, optional): Stride of the convolution. Default: 1
        padding (int or tuple, optional): Zero-padding added to both sides of
            the input. Default: 0
        padding_mode (string, optional): ``'zeros'``, ``'reflect'``,
            ``'replicate'`` or ``'circular'``. Default: ``'zeros'``
        dilation (int or tuple, optional): Spacing between kernel elements.
            Default: 1
        groups (int, optional): Number of blocked connections from input
            channels to output channels. Default: 1
        bias (bool, optional): If ``True``, adds a learnable bias to the
            output. Default: ``True``
        use_deform: If ``True``, replace convolution with deformable
            convolution. Default: ``False``.
    """

    def __init__(self,
                 in_channels,
                 out_channels,
                 kernel_size,
                 stride=1,
                 padding=0,
                 dilation=1,
                 groups=1,
                 bias=True,
                 use_deform=False):
        super().__init__(
            in_channels,
            out_channels,
            kernel_size,
            stride=stride,
            padding=padding,
            dilation=dilation,
            groups=groups,
            bias=bias)
        self.use_deform = use_deform
        self.weight_diff = nn.Parameter(torch.Tensor(self.weight.size()))
        self.pre_context = nn.Conv2d(
            self.in_channels, self.in_channels, kernel_size=1, bias=True)
        self.post_context = nn.Conv2d(
            self.out_channels, self.out_channels, kernel_size=1, bias=True)
        if self.use_deform:
            self.offset_s = nn.Conv2d(
                self.in_channels,
                18,
                kernel_size=3,
                padding=1,
                stride=stride,
                bias=True)
            self.offset_m = nn.Conv2d(
                self.in_channels,
                18,
                kernel_size=3,
                padding=1,
                stride=stride,
                bias=True)
            self.offset_l = nn.Conv2d(
                self.in_channels,
                18,
                kernel_size=3,
                padding=1,
                stride=stride,
                bias=True)


        self.node_weight = nn.Parameter(torch.ones(3, dtype=torch.float32), requires_grad=True)
        self.node_weight_relu = nn.ReLU()
        self.weight_diff.data.zero_()
        constant_init(self.pre_context, 0)
        constant_init(self.post_context, 0)
        if self.use_deform:
           constant_init(self.offset_s, 0)
           constant_init(self.offset_l, 0)
           constant_init(self.offset_m, 0)



def forward(self, x):
    # pre-context
    avg_x = F.adaptive_avg_pool2d(x, output_size=1)
    avg_x = self.pre_context(avg_x)
    avg_x = avg_x.expand_as(x)
    x = x + avg_x
    # switch
    avg_x = F.pad(x, pad=(2, 2, 2, 2), mode='reflect')
    avg_x = F.avg_pool2d(avg_x, kernel_size=5, stride=1, padding=0)
    # sac
    weight = self._get_weight(self.weight)
    if self.use_deform:
        offset = self.offset_s(avg_x)
        out_s = deform_conv2d(x, offset, weight, self.stride, self.padding,
                              self.dilation, self.groups, 1)
    else:
        if TORCH_VERSION < '1.5.0' or TORCH_VERSION == 'parrots':
            out_s = super().conv2d_forward(x, weight)
        else:
            out_s = super()._conv_forward(x, weight)
    ori_p = self.padding
    ori_d = self.dilation
    self.padding = tuple(3 * p for p in self.padding)
    self.dilation = tuple(3 * d for d in self.dilation)
    weight = weight + self.weight_diff
    if self.use_deform:
        offset = self.offset_l(avg_x)
        out_l = deform_conv2d(x, offset, weight, self.stride, self.padding,
                              self.dilation, self.groups, 1)
    else:
        if TORCH_VERSION < '1.5.0' or TORCH_VERSION == 'parrots':
            out_l = super().conv2d_forward(x, weight)
        else:
            out_l = super()._conv_forward(x, weight)

    self.padding = tuple(5 * p for p in ori_p)
    self.dilation = tuple(5 * d for d in ori_d)
    weight = weight + self.weight_diff
    if self.use_deform:
        offset = self.offset_m(avg_x)
        out_m = deform_conv2d(x, offset, weight, self.stride, self.padding,
                              self.dilation, self.groups, 1)
    else:
        if TORCH_VERSION < '1.5.0' or TORCH_VERSION == 'parrots':
            out_m= super().conv2d_forward(x, weight)
        else:
            out_m = super()._conv_forward(x, weight)
    w = self.node_weight_relu(self.node_weight(avg_x))
    weight = w / (torch.sum(w, dim=0) + 1e-4)
    out = out_s*weight[0]+ out_m*weight[1]+out_l*weight[2]
    self.padding = ori_p
    self.dilation = ori_d
    # post-context
    avg_x = F.adaptive_avg_pool2d(out, output_size=1)
    avg_x = self.post_context(avg_x)
    avg_x = avg_x.expand_as(out)
    out = out + avg_x
    return out
